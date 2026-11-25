"""Job orchestration: extract + persist + publish, batch resume, cancellation and recovery.

``process_file`` is the programmatic entry point (the CLI and evaluation call it). It never raises for a
document or runtime failure: it returns a result dict with ``status`` and ``exit_code`` and leaves a
recoverable job row and artifact root behind.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import time
from pathlib import Path
from typing import Callable

from . import exports
from .artifacts import write_json
from .config import ProcessingError, Settings, digest
from .contracts import Document
from .store import DEFAULT_FORMATS, Job, Persisted, Store

EXIT_OK, EXIT_INTERNAL, EXIT_USAGE, EXIT_INCOMPLETE, EXIT_FAILED = 0, 1, 2, 3, 4
EXIT_MISSING_RUNTIME, EXIT_CONFLICT, EXIT_INVALID, EXIT_EXPORT_PENDING, EXIT_CANCELLED = 5, 6, 7, 8, 130
MISSING_RUNTIME = {"missing_model", "hybrid_unavailable", "unsupported_runtime", "missing_tool", "provision_failed",
                   "parser_setup_required", "integrity_error", "domain_unavailable"}
CONFLICT = {"stale_base", "lease_lost"}
INVALID = {"no_supported_files", "unknown_document", "unknown_version", "invalid_correction", "document_mismatch", "invalid_format",
           "invalid_input"}
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".pdf", ".heic", ".heif"}
HEARTBEAT_SECONDS = 15.0
Progress = Callable[[dict], None]

_HINTS = {
    "missing_model": "Run 'vision-tables doctor' to see missing components, then 'vision-tables models prepare'.",
    "hybrid_unavailable": "The hybrid route needs the full local parser; use --pipeline direct or see 'doctor'.",
    "unsupported_runtime": "The pinned local runtime needs an Apple Silicon Mac.",
    "no_extraction": "No page yielded fields. Inspect readings/*.json and job.json under the artifact root.",
    "source_too_large": "Reduce the file or raise --max-bytes (bounded at 512 MiB); the file was not read.",
    "invalid_input": "Check the path or file named in the message and rerun; nothing was stored.",
    "stale_base": "Run 'vision-tables documents show DOCUMENT_ID', export the latest version, review it and "
                  "set base_version to it.",
    "invalid_correction": "Fix the corrections file (see review/corrections.schema.json in the review bundle); "
                          "nothing was applied.",
    "unknown_document": "List documents with 'vision-tables documents list' (check --state-dir).",
    "unknown_version": "List versions with 'vision-tables documents show DOCUMENT_ID'.",
    "document_mismatch": "The corrections file's document_id must equal --document.",
    "invalid_format": "Use json, csv, review, sqlite or all.",
    "domain_unavailable": "Install a build that includes the domain module.",
    "lease_lost": "Another process took this job over; run 'vision-tables jobs show JOB_ID'.",
}


def exit_code(code: str | None) -> int:
    if code in MISSING_RUNTIME:
        return EXIT_MISSING_RUNTIME
    if code in CONFLICT:
        return EXIT_CONFLICT
    if code in INVALID:
        return EXIT_INVALID
    return EXIT_FAILED


def error_payload(code: str, message: str, job: Job | dict | None = None) -> dict:
    root = job.artifact_root if isinstance(job, Job) else (job or {}).get("artifact_root")
    return {"code": code, "message": message, "artifact_root": str(root) if root else None,
            "next": _HINTS.get(code, "Inspect job.json and the stage artifacts under the artifact root; fix the "
                                     "input or settings and rerun; the original job is kept for evidence.")}


def file_sha256(path: Path, max_bytes: int | None = None) -> str:
    """Hash a regular file, refusing links, devices and anything over ``max_bytes`` before/while reading."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ProcessingError("invalid_input", f"Cannot read {Path(path).name}: {exc.strerror or exc}") from exc
    with os.fdopen(fd, "rb") as fp:
        info = os.fstat(fp.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ProcessingError("invalid_input", f"{Path(path).name} is not a regular file.")
        if max_bytes is not None and info.st_size > max_bytes:
            raise ProcessingError("source_too_large", f"{Path(path).name} is {info.st_size} bytes; the limit is "
                                  f"{max_bytes} (--max-bytes). The file was not read.")
        digest_, total = hashlib.sha256(), 0
        while block := fp.read(1 << 20):
            total += len(block)
            if max_bytes is not None and total > max_bytes:  # grew while reading
                raise ProcessingError("source_too_large", f"{Path(path).name} exceeds --max-bytes {max_bytes}.")
            digest_.update(block)
    return digest_.hexdigest()


class Heartbeat:
    """Renews the job lease and relays pipeline checkpoints as progress events."""

    def __init__(self, store: Store, job: Job, progress: Progress | None, item: tuple[str, str] | None = None):
        self.store, self.job, self.progress, self.item = store, job, progress, item
        self.lost = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self):
        last_beat, seen = time.monotonic(), None
        while not self._stop.wait(0.5):
            if time.monotonic() - last_beat >= HEARTBEAT_SECONDS:
                last_beat = time.monotonic()
                try:
                    self.lost = self.lost or not self.store.heartbeat(self.job.job_id, self.job.token, self.item)
                except Exception:  # a transient DB error must not kill extraction; ownership is rechecked at commit
                    pass
            if self.progress:
                try:
                    state = json.loads((self.job.artifact_root / "job.json").read_text())
                except (OSError, ValueError):
                    continue
                view = {k: state.get(k) for k in ("stage", "status", "processed_pages") if k in state}
                if view != seen:
                    seen = view
                    self.progress({"event": "progress", "job_id": self.job.job_id, **view})


def stage_documents(job: Job, documents: list[Document]) -> None:
    write_json(job.artifact_root / "documents.json", {"job_id": job.job_id,
                                                      "documents": [d.model_dump(mode="json") for d in documents]})


def load_staged(artifact_root: Path) -> list[Document] | None:
    try:
        data = json.loads((artifact_root / "documents.json").read_text())
        return [Document.model_validate(d) for d in data["documents"]]
    except (OSError, ValueError, KeyError):
        return None


def _coverage(doc: Document) -> dict:
    cov = doc.coverage
    return {"scope": doc.scope, "processed_pages": cov.processed_pages, "skipped_pages": cov.skipped_pages,
            "source_skipped_pages": cov.source_skipped_pages, "incomplete_pages": cov.incomplete_pages,
            "suspected_capture": cov.suspected_capture, "original_capture": cov.original_capture,
            "source_page_count": doc.source.page_count}


def _summary(p: Persisted, export: dict | None) -> dict:
    d = p.document
    return {"document_id": p.document_id, "version": p.version, "base_version": p.base_version,
            "parent_version": p.parent_version, "action": p.action, "created_version": p.created_version,
            "doc_type": d.doc_type, "validation": d.validation, "approval": d.approval,
            "review_required": d.approval != "human_approved", "coverage": _coverage(d),
            "issue_count": len(d.issues), "issue_codes": sorted({i.code for i in d.issues}),
            "possible_duplicates": p.possible_duplicates, "export": export}


def _finish(store: Store, job_id: str, token: str, documents: list[Document], output: Path,
            progress: Progress | None) -> dict:
    """Persist (idempotently) and publish. The only step that can leave work pending is publication."""
    persisted = store.persist_candidates(job_id, token, documents)
    if progress:
        progress({"event": "persisted", "job_id": job_id,
                  "documents": [{"document_id": p.document_id, "version": p.version, "action": p.action}
                                for p in persisted]})
    summaries, pending = [], []
    for p in persisted:
        try:
            info = exports.publish(store, p.document_id, p.version, output, DEFAULT_FORMATS)
            summaries.append(_summary(p, {"state": "published", **info}))
        except (OSError, ProcessingError) as exc:
            summaries.append(_summary(p, {"state": "pending", "error": str(exc), "output_dir": str(output)}))
            pending.append(p.document_id)
    incomplete = any(s["validation"] == "incomplete" for s in summaries)
    status = "incomplete" if incomplete else "completed"
    if pending:
        store.release(job_id, token)
    else:
        store.set_job(job_id, token, status)
    return {"status": "export_pending" if pending else status,
            "exit_code": EXIT_EXPORT_PENDING if pending else (EXIT_INCOMPLETE if incomplete else EXIT_OK),
            "documents": summaries}


def process_file(store: Store, path: Path, output: Path, settings: Settings, *, progress: Progress | None = None,
                 batch: tuple[str, str, str] | None = None) -> dict:
    """Extract one source, persist its candidates transactionally and publish exports."""
    from . import pipeline

    path, output = Path(path), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    job = store.create_job(kind="batch_item" if batch else "extract", input_path=str(path.resolve()),
                           output_dir=str(output), settings_json=json.dumps(settings.model_dump(mode="json"),
                                                                            sort_keys=True),
                           batch_id=batch[0] if batch else None)
    if batch:
        store.item_job(batch[0], batch[1], batch[2], job.job_id)
    head = {"job": {"job_id": job.job_id, "artifact_root": str(job.artifact_root)},
            "source": {"path": str(path)}}
    if progress:
        progress({"event": "job_started", "job_id": job.job_id, "artifact_root": str(job.artifact_root)})
    item = (batch[0], batch[1]) if batch else None
    try:
        with Heartbeat(store, job, progress, item) as beat:
            documents = pipeline.extract(path, output, settings, job_id=job.job_id)
            if beat.lost:
                raise ProcessingError("lease_lost", "The job lease expired while extracting.")
            stage_documents(job, documents)
            store.set_job(job.job_id, job.token, "persisting", source_sha256=documents[0].source.sha256)
            result = _finish(store, job.job_id, job.token, documents, output, progress)
        head["source"]["sha256"] = documents[0].source.sha256
    except KeyboardInterrupt:
        row = store.get_job(job.job_id) or {}
        if row.get("state") == "persisted":  # committed: only publication is left, recoverable at once
            _quiet(store.release, job.job_id, job.token)
        else:
            _quiet(store.set_job, job.job_id, job.token, "cancelled", error_code="cancelled",
                   error_message="Cancelled by signal; staged artifacts are kept.")
        staged = (job.artifact_root / "documents.json").is_file()
        return {"status": "cancelled", "exit_code": EXIT_CANCELLED, **head,
                "error": {"code": "cancelled", "artifact_root": str(job.artifact_root),
                          "message": "Cancelled by signal; no partial version was committed.",
                          "next": "Run 'vision-tables jobs recover' to finish a staged result." if staged
                          else "Rerun the same extract command; inference restarts from the source."}}
    except ProcessingError as exc:
        _quiet(store.set_job, job.job_id, job.token, "failed", error_code=exc.code, error_message=str(exc))
        return {"status": "failed", "exit_code": exit_code(exc.code), **head,
                "error": error_payload(exc.code, str(exc), job)}
    except Exception as exc:  # unexpected defect: still leave a failed, inspectable job
        _quiet(store.set_job, job.job_id, job.token, "failed", error_code="internal_error",
               error_message=f"{type(exc).__name__}: {exc}")
        return {"status": "failed", "exit_code": EXIT_INTERNAL, **head,
                "error": error_payload("internal_error", f"{type(exc).__name__}: {exc}", job)}
    return {**result, **head}


def _quiet(fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except Exception:
        pass


# -- recovery ---------------------------------------------------------------------------------
def recover_job(store: Store, row: dict, token: str, progress: Progress | None = None) -> dict:
    root = store.artifact_root(row["job_id"])
    documents = load_staged(root)
    if documents is None:
        store.set_job(row["job_id"], token, "interrupted", error_code="interrupted",
                      error_message="No staged result; inference must be rerun.")
        return {"job_id": row["job_id"], "action": "needs_rerun", "input_path": row["input_path"]}
    result = _finish(store, row["job_id"], token, documents, Path(row["output_dir"]), progress)
    return {"job_id": row["job_id"], "action": "recovered", **result}


def recover(store: Store, progress: Progress | None = None) -> dict:
    """Finish what an interrupted process left behind. Never touches a live owner's jobs."""
    report = {"jobs": [], "exports": []}
    store.reap_dead_owners()
    for row, token in store.claim_expired_jobs():
        report["jobs"].append(recover_job(store, row, token, progress))
    for row in store.list_jobs():
        if row["state"] in ("cancelled", "interrupted") and load_staged(store.artifact_root(row["job_id"])):
            token = store.claim_job(row["job_id"])
            if token:
                report["jobs"].append(recover_job(store, row, token, progress))
    report["exports"] = exports.publish_pending(store)
    bad = [e for e in report["exports"] if e["state"] != "published"]
    report["status"] = "export_pending" if bad else ("needs_rerun" if any(
        j["action"] == "needs_rerun" for j in report["jobs"]) else "ok")
    report["exit_code"] = EXIT_EXPORT_PENDING if bad else EXIT_OK
    return report


# -- batch --------------------------------------------------------------------------------------
def discover(input_dir: Path) -> tuple[list[str], list[str]]:
    """Top-level regular files, sorted. Returns (supported, unsupported). Dotfiles and symlinks are ignored."""
    supported, other = [], []
    for entry in sorted(Path(input_dir).iterdir(), key=lambda p: p.name):
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_file():
            continue
        (supported if entry.suffix.lower() in SUPPORTED_SUFFIXES else other).append(entry.name)
    return supported, other


def effective_fingerprint(settings: Settings) -> str:
    """The same fingerprint extraction records as run.config_hash (schema, pipeline, model, runtime, settings)."""
    from . import models, pipeline
    return pipeline.extraction_fingerprint(settings, models.runtime_info(settings))


def batch_id_for(input_dir: Path, output: Path, fingerprint: str) -> str:
    """A batch resumes only under the same effective version: an upgrade starts a new batch."""
    return "batch-" + digest({"input": str(input_dir), "output": str(output), "fingerprint": fingerprint})[:24]


def run_batch(store: Store, input_dir: Path, output: Path, settings: Settings, *, progress: Progress | None = None,
              retry_failed: bool = False, retry_incomplete: bool = False) -> dict:
    input_dir, output = Path(input_dir).resolve(), Path(output).resolve()
    if not input_dir.is_dir():
        raise ProcessingError("invalid_input", f"{input_dir} is not a directory.")
    output.mkdir(parents=True, exist_ok=True)
    supported, unsupported = discover(input_dir)
    fingerprint = effective_fingerprint(settings)  # once per batch; models.runtime_info may spawn a subprocess
    batch_id = batch_id_for(input_dir, output, fingerprint)
    store.ensure_batch(batch_id, str(input_dir), str(output),
                       json.dumps({"fingerprint": fingerprint, "settings": settings.model_dump(mode="json")},
                                  sort_keys=True), supported + unsupported)
    store.reap_dead_owners()
    rows = {r["name"]: r for r in store.batch_items(batch_id)}
    cancelled = False
    for name in supported + unsupported:
        row = rows[name]
        if name in unsupported:
            token = store.claim_item(batch_id, name, ("skipped",))
            if token:
                store.finish_item(batch_id, name, token, "skipped", job_id=None, source_sha256=None,
                                  detail={"reason": "unsupported_extension", "extension": Path(name).suffix})
            continue
        source = input_dir / name
        try:
            sha = file_sha256(source, settings.max_bytes)
        except ProcessingError as exc:  # unread: a later run retries once the file is acceptable
            token = store.claim_item(batch_id, name, ("failed",))
            if token:
                store.finish_item(batch_id, name, token, "failed", job_id=None, source_sha256=None,
                                  detail={"status": "failed", "exit_code": exit_code(exc.code),
                                          "error": error_payload(exc.code, str(exc))})
            continue
        changed = row["source_sha256"] not in (None, sha)
        retry = ("cancelled",) + (("failed",) if retry_failed else ()) + (("incomplete",) if retry_incomplete else ())
        if row["state"] == "failed" and row["job_id"] is None:  # rejected before any read; now acceptable
            retry += ("failed",)
        if changed:
            retry = ("completed", "incomplete", "failed", "skipped", "cancelled")
        token = store.claim_item(batch_id, name, retry)
        if token is None:
            continue  # completed, owned by a live worker, or failed without --retry-failed
        if progress:
            progress({"event": "item_started", "batch_id": batch_id, "name": name})
        previous = rows[name].get("job_id")
        result = None
        if previous and (prior := store.get_job(previous)) and prior["state"] in ("running", "persisting", "persisted"):
            claimed = store.claim_expired_jobs(only=previous)
            if claimed:
                recovered = recover_job(store, *claimed[0], progress)
                result = recovered if recovered["action"] == "recovered" else None
        if result is None:
            result = process_file(store, source, output, settings, progress=progress, batch=(batch_id, name, token))
        state = {"completed": "completed", "incomplete": "incomplete", "cancelled": "cancelled"}.get(
            result["status"], "failed")
        if result["status"] == "export_pending":
            state = "failed"
        store.finish_item(batch_id, name, token, state, job_id=result.get("job", {}).get("job_id", previous),
                          source_sha256=sha, detail={k: v for k, v in result.items() if k in
                                                     ("status", "documents", "error", "job", "exit_code")})
        if state == "cancelled":
            cancelled = True
            break
    items = store.batch_items(batch_id)
    items = [i for i in items if i["name"] in supported + unsupported]
    counts: dict[str, int] = {}
    for i in items:
        counts[i["state"]] = counts.get(i["state"], 0) + 1
    if cancelled:
        status, code = "cancelled", EXIT_CANCELLED
    elif not supported:
        status, code = "failed", EXIT_INVALID  # nothing was extractable; never a silent success
    elif counts.get("failed") or counts.get("pending") or counts.get("running"):
        status, code = "failed" if counts.get("failed") else "running", EXIT_FAILED
    elif counts.get("incomplete") or counts.get("skipped"):  # a skipped input means the directory was not fully read
        status, code = "incomplete", EXIT_INCOMPLETE
    else:
        status, code = "completed", EXIT_OK
    store.set_batch(batch_id, "running" if status == "running" else status)
    report = {"status": status, "exit_code": code, "batch_id": batch_id, "input_dir": str(input_dir),
              "output_dir": str(output), "counts": dict(sorted(counts.items())), "fingerprint": fingerprint,
              "items": [{"name": i["name"], "state": i["state"], "job_id": i["job_id"],
                         "source_sha256": i["source_sha256"], **(json.loads(i["detail_json"]) if i["detail_json"]
                                                                  else {})} for i in items]}
    if unsupported and supported:
        report["skipped_inputs"] = [{"name": n, "reason": "unsupported_extension"} for n in unsupported]
        report["note"] = (f"{len(unsupported)} unsupported file(s) were skipped, so the batch is incomplete: "
                          "convert them to JPEG, PNG, PDF or HEIC and rerun the same command.")
    if not supported:
        report["error"] = error_payload("no_supported_files", f"{input_dir} has no supported files "
                                        f"({', '.join(sorted(SUPPORTED_SUFFIXES))}); {len(unsupported)} skipped.")
        report["error"]["next"] = "Point batch at a directory of JPEG, PNG, PDF or HEIC files."
    status_file = output / f"{batch_id}.status.json"
    write_json(status_file, report)
    report["status_file"] = str(status_file)
    return report
