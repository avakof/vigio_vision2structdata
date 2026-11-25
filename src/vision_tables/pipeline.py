"""Bounded in-process pipeline with one durable artifact directory per attempt."""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from jsonschema import Draft202012Validator

from . import models
from .artifacts import write_json
from .config import PIPELINE_VERSION, SCHEMA_VERSION, ProcessingError, Settings, digest
from .contracts import Document, Ingested, Issue, PageReading, Run


def page_scopes(total: int, settings: Settings, processed: set[int]) -> list[list[int]]:
    groups = [list(group) for group in settings.page_groups]
    if any(page > total for group in groups for page in group):
        raise ProcessingError("invalid_page_group", "A requested page is outside the source.")
    grouped = {page for group in groups for page in group}
    groups.extend([page] for page in sorted(processed - grouped))
    return sorted(groups, key=lambda group: group[0])


def extract(path: Path, output: Path, settings: Settings, *, job_id: str | None = None) -> list[Document]:
    """Read a source. Callers persist and publish canonical versions transactionally."""
    from . import domain, vision

    job_id = job_id or uuid.uuid4().hex
    if not job_id.isascii() or not job_id.replace("-", "").isalnum() or len(job_id) > 100:
        raise ProcessingError("invalid_job", "Invalid local job identifier.")
    root = settings.state_dir.resolve() / "jobs" / job_id
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    progress = {"job_id": job_id, "stage": "inspect", "status": "running", "processed_pages": []}
    started = time.monotonic()

    def checkpoint(**values):
        progress.update(values)
        write_json(root / "job.json", progress)

    try:
        checkpoint()
        ingestion = vision.inspect_source(Path(path), root, settings)
        write_json(root / "ingestion.json", ingestion.model_dump(mode="json"))
        pages = {p.number: p for p in ingestion.pages}
        skipped = set(ingestion.skipped_pages)
        if (len(pages) != len(ingestion.pages) or len(pages) > settings.max_pages or not pages
                or pages.keys() & skipped or len(pages) + len(skipped) != ingestion.source.page_count
                or any(n > ingestion.source.page_count for n in pages.keys() | skipped)):
            raise ProcessingError("invalid_ingestion", "The source has unaccounted or invalid pages.")
        scopes = page_scopes(ingestion.source.page_count, settings, set(pages))
        checkpoint(stage="read", source_sha256=ingestion.source.sha256, skipped_pages=sorted(skipped))
        info = models.runtime_info(settings)
        if not info["ready"]:
            message = "Run vision-tables doctor and models prepare to inspect missing local components."
            if settings.pipeline == "hybrid" and not info["components"].get("parser", {}).get("ready"):
                raise ProcessingError("hybrid_unavailable", str(info["components"].get("parser", {}).get("reason", message)))
            raise ProcessingError("missing_model", message)
        content = settings.content_settings()
        identity = {"schema": SCHEMA_VERSION, "pipeline_version": PIPELINE_VERSION,
                    "settings": content, "model": info["model"], "revision": info["revision"],
                    "runtime": info["versions"], "components": info["components"]}
        config_hash = digest(identity)
        readings = {}
        any_records = False
        for n, page in sorted(pages.items()):
            local = [r for r in ingestion.regions if r.page == n]
            try:
                reading = vision.read_page(page, local, root, settings)
                if reading.page != n or any(region.page != n for region in reading.regions):
                    raise ProcessingError("invalid_page_reference", "Reader returned an unrelated page.")
                if not any(r.fields for r in reading.records):
                    reading.incomplete = True
                    reading.issues.append(Issue(code="no_fields", message="No supported fields were read.", page=n))
                else:
                    any_records = True
            except ProcessingError as exc:
                reading = PageReading(page=n, doc_type="unknown", incomplete=True,
                                      issues=[Issue(code=exc.code, message=str(exc), page=n)])
            write_json(root / "readings" / f"{n:04d}.json", reading.model_dump(mode="json"))
            readings[n] = reading
            checkpoint(processed_pages=sorted(readings))
        if not any_records:
            raise ProcessingError("no_extraction", "No page yielded fields. Inspect the per-page reading artifacts.")
        checkpoint(stage="assemble")
        documents = []
        for scope in scopes:
            scoped = Ingested(source=ingestion.source, pages=[pages[n] for n in scope if n in pages],
                              regions=[r for r in ingestion.regions if r.page in scope],
                              issues=[i for i in ingestion.issues if i.page is None or i.page in scope],
                              skipped_pages=sorted(skipped.intersection(scope)), source_skipped_pages=sorted(skipped))
            collected = [readings[n] for n in scope if n in readings]
            if not collected:
                continue
            if skipped - set(scope):
                scoped.issues.append(Issue(code="source_page_limit", message="Source pages skipped: " +
                                            ", ".join(map(str, sorted(skipped - set(scope))))))
            doc_id = "doc-" + digest([ingestion.source.sha256, scope])[:32]
            run = Run(job_id=job_id, pipeline=settings.pipeline, model=info["model"], revision=info["revision"],
                      settings=content, config_hash=config_hash, runtime=info,
                      calls=[call for reading in collected for call in reading.calls])
            candidate = domain.assemble(scoped, collected, scope, run, doc_id)
            validated = Document.model_validate(candidate.model_dump(mode="json"))
            Draft202012Validator(Document.model_json_schema()).validate(validated.model_dump(mode="json"))
            documents.append(validated)
        if not documents:
            raise ProcessingError("no_documents", "No document could be assembled.")
        status = "incomplete" if skipped or any(d.validation == "incomplete" for d in documents) else "read"
        checkpoint(stage="assembled", status=status, document_ids=[d.document_id for d in documents],
                   elapsed_seconds=time.monotonic() - started)
        return documents
    except BaseException as exc:
        checkpoint(status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                   error_code=getattr(exc, "code", type(exc).__name__), error=str(exc),
                   elapsed_seconds=time.monotonic() - started)
        raise
