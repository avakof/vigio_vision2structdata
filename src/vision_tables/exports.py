"""Deterministic JSON, relational CSV, review bundle and SQLite exports from one stored snapshot.

Every format is derived from the immutable snapshot bytes stored for (document_id, version). Files are
published atomically (temp file or staging directory, fsync, rename) under
``<output>/<document_id>/v<NNNN>/`` and ``manifest.json`` is written last: a version directory without
a manifest, or with a pending ``exports`` row, is an unfinished publication that ``jobs recover`` retries.
"""
from __future__ import annotations

import csv
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import uuid
from pathlib import Path

from .config import ProcessingError
from .contracts import CorrectionSet, Document
from .migrations import apply_migrations
from .store import DEFAULT_FORMATS, Store, copy_version, digest_bytes
from .vocabulary import FIELDS

FORMATS = ("json", "csv", "review", "sqlite")
BUNDLE_FORMAT = "vision-tables-review/1"
MANIFEST_FORMAT = "vision-tables-export/1"

_NUMERIC = re.compile(r"^[+-]?\d[\d .,  ]*$")
_LEADING = " \t\r\n\x00\x0b\x0c "


def csv_cell(value: object) -> str:
    """Render one CSV cell. Cells a spreadsheet could execute get a leading apostrophe.

    Plain signed numbers such as -12.50 are not formulas and stay intact. Only this presentation layer
    changes: canonical JSON and SQLite keep the original text.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    if text and (text[0] in "=+-@\t\r\n" or text.lstrip(_LEADING)[:1] in ("=", "+", "-", "@")):
        if not _NUMERIC.match(text):
            return "'" + text
    return text


def _csv(header: list[str], rows: list[list[object]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows([[csv_cell(cell) for cell in row] for row in rows])
    return out.getvalue().encode("utf-8")


def _j(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def render_tables(doc: Document) -> dict[str, bytes]:
    """Relational tables joined by document_id, entity_id and observation_id, in stable order."""
    did = doc.document_id
    kinds = {e.id: e.kind for e in doc.entities}
    entities = sorted(doc.entities, key=lambda e: (e.page, e.ordinal, e.id))
    rank = {e.id: i for i, e in enumerate(entities)}
    order = {kind: {f: i for i, f in enumerate(fields)} for kind, fields in FIELDS.items()}
    observations = sorted(doc.observations, key=lambda o: (rank[o.entity_id],
                          order[kinds[o.entity_id]].get(o.field, 999), o.id))
    cov = doc.coverage
    unresolved = sum(o.state != "readable" for o in observations)
    tables = {
        "documents.csv": _csv(
            ["document_id", "version", "parent_version", "doc_type", "validation", "approval", "scope",
             "source_sha256", "media_type", "size_bytes", "page_count", "processed_pages", "skipped_pages",
             "source_skipped_pages", "incomplete_pages", "suspected_capture", "original_capture", "pipeline",
             "model", "revision", "schema_version", "pipeline_version", "config_hash", "job_id", "issue_count",
             "unresolved_observation_count"],
            [[did, doc.version, doc.parent_version, doc.doc_type, doc.validation, doc.approval, _j(doc.scope),
              doc.source.sha256, doc.source.media_type, doc.source.size_bytes, doc.source.page_count,
              _j(cov.processed_pages), _j(cov.skipped_pages), _j(cov.source_skipped_pages),
              _j(cov.incomplete_pages), cov.suspected_capture, cov.original_capture, doc.run.pipeline,
              doc.run.model, doc.run.revision, doc.run.schema_version, doc.run.pipeline_version,
              doc.run.config_hash, doc.run.job_id, len(doc.issues), unresolved]]),
        "pages.csv": _csv(
            ["document_id", "page", "width", "height", "unit", "image_path", "image_width", "image_height",
             "image_to_original", "text_layer"],
            [[did, p.number, p.width, p.height, p.unit, p.image_path, p.image_width, p.image_height,
              _j(p.image_to_original), p.text_layer] for p in sorted(doc.pages, key=lambda p: p.number)]),
        "regions.csv": _csv(
            ["document_id", "region_id", "page", "order", "kind", "text", "language", "writing", "box_xyxy",
             "box_precision", "artifact_path", "artifact_sha256"],
            [[did, r.id, r.page, r.order, r.kind, r.text, r.language, r.writing,
              _j(r.box.xyxy) if r.box else None, r.box.precision if r.box else None, r.artifact_path,
              r.artifact_sha256] for r in sorted(doc.regions, key=lambda r: (r.page, r.order, r.id))]),
        "entities.csv": _csv(
            ["document_id", "entity_id", "kind", "parent_id", "page", "ordinal"],
            [[did, e.id, e.kind, e.parent_id, e.page, e.ordinal] for e in entities]),
        "observations.csv": _csv(
            ["document_id", "observation_id", "entity_id", "entity_kind", "field", "raw", "value", "state",
             "origin", "language", "reason", "alternatives", "derivation", "derivation_inputs", "evidence_count"],
            [[did, o.id, o.entity_id, kinds[o.entity_id], o.field, o.raw, o.value, o.state, o.origin, o.language,
              o.reason, _j(o.alternatives), o.derivation.calculation if o.derivation else None,
              _j(o.derivation.inputs) if o.derivation else None, len(o.evidence)] for o in observations]),
        "evidence.csv": _csv(
            ["document_id", "observation_id", "seq", "page", "region_id", "quote", "channel", "attempt_id",
             "source_sha256", "job_id", "source_path", "page_image_path"],
            [[did, o.id, i, ref.page, ref.region_id, ref.quote, ref.channel, ref.attempt_id, doc.source.sha256,
              doc.run.job_id, doc.source.original_path,
              next(p.image_path for p in doc.pages if p.number == ref.page)]
             for o in observations for i, ref in enumerate(o.evidence)]),
        "issues.csv": _csv(
            ["document_id", "issue_seq", "code", "message", "page", "observation_ids"],
            [[did, i, x.code, x.message, x.page, _j(x.observation_ids)] for i, x in enumerate(doc.issues)]),
        "corrections.csv": _csv(
            ["document_id", "seq", "base_version", "reviewer", "approve", "observation_id", "value", "raw",
             "state", "reason"],
            [[did, i, c.base_version, c.reviewer, c.approve, *(
                [ch.observation_id, ch.value, ch.raw, ch.state, ch.reason] if ch else [None] * 5)]
             for i, c in enumerate(doc.corrections) for ch in (c.changes or [None])]),
    }
    tables["structural_corrections.csv"] = _csv(
        ["document_id", "event", "base_version", "reviewer", "operation", "entity_id", "reason", "details_json"],
        [[did, i, correction.base_version, correction.reviewer, op.op, op.entity_id, op.reason,
          _j(op.model_dump(mode="json"))]
         for i, correction in enumerate(doc.corrections) for op in correction.structure])
    by_obs = {}
    for obs in observations:
        by_obs.setdefault(obs.entity_id, {})[obs.field] = obs
    for kind in FIELDS:
        rows = [e for e in entities if e.kind == kind]
        if not rows:
            continue
        header = ["document_id", "entity_id", "parent_id", "parent_kind", "page", "ordinal"]
        for field in FIELDS[kind]:
            header += [field, f"{field}__state", f"{field}__observation_id"]
        body = []
        for e in rows:
            row = [did, e.id, e.parent_id, kinds.get(e.parent_id), e.page, e.ordinal]
            for field in FIELDS[kind]:
                obs = by_obs.get(e.id, {}).get(field)
                row += [obs.value, obs.state, obs.id] if obs else [None, None, None]
            body.append(row)
        tables[f"table_{kind}.csv"] = _csv(header, body)
    return tables


def _file_info(store: Store, job_id: str, relative: str | None) -> dict:
    path = store.resolve_artifact(job_id, relative)
    return {"relative_path": relative, "path": str(path) if path else None,
            "exists": bool(path and path.is_file())}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fp:  # read-only: review never modifies the source
        while block := fp.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def review_files(store: Store, doc: Document, snapshot_sha: str) -> dict[str, bytes]:
    """Structured issues, resolvable source/page/crop references, alternatives and the corrections format."""
    job = doc.run.job_id
    pages = {p.number: p for p in doc.pages}
    regions = {r.id: r for r in doc.regions}
    kinds = {e.id: e for e in doc.entities}
    observations = {o.id: o for o in doc.observations}
    source = _file_info(store, job, doc.source.original_path)
    source["sha256"] = doc.source.sha256
    source["sha256_verified"] = (_sha256_file(Path(source["path"])) == doc.source.sha256) if source["exists"] else None

    def page_ref(number: int) -> dict:
        page = pages.get(number)
        if page is None:
            return {"page": number, "image": None}
        return {"page": number, "width": page.width, "height": page.height, "unit": page.unit,
                "image": _file_info(store, job, page.image_path) | {"width": page.image_width,
                                                                    "height": page.image_height},
                "image_to_original": list(page.image_to_original), "text_layer": page.text_layer}

    def region_ref(region_id: str | None) -> dict | None:
        region = regions.get(region_id) if region_id else None
        if region is None:
            return None
        return {"region_id": region.id, "kind": region.kind, "writing": region.writing,
                "box": region.box.model_dump(mode="json") if region.box else None,
                "crop": _file_info(store, job, region.artifact_path) | {"sha256": region.artifact_sha256}
                if region.artifact_path else None,
                "crop_to_original": list(region.crop_to_original) if region.crop_to_original else None}

    issue_codes: dict[str, list[str]] = {}
    for issue in doc.issues:
        for obs_id in issue.observation_ids:
            issue_codes.setdefault(obs_id, []).append(issue.code)

    def item(obs) -> dict:
        entity = kinds[obs.entity_id]
        return {"observation_id": obs.id, "entity_id": obs.entity_id, "entity_kind": entity.kind,
                "parent_id": entity.parent_id, "field": obs.field, "raw": obs.raw, "value": obs.value,
                "state": obs.state, "origin": obs.origin, "alternatives": obs.alternatives, "reason": obs.reason,
                "issue_codes": sorted(issue_codes.get(obs.id, [])),
                "derivation": obs.derivation.model_dump(mode="json") if obs.derivation else None,
                "evidence": [{"page": ref.page, "quote": ref.quote, "channel": ref.channel,
                              "attempt_id": ref.attempt_id, "page_reference": page_ref(ref.page),
                              "region": region_ref(ref.region_id)} for ref in obs.evidence]}

    flagged = sorted({o.id for o in doc.observations if o.state != "readable"} | issue_codes.keys())
    first = next((observations[i] for i in flagged if observations[i].origin != "calculated"), None)
    reviewer_note = "REPLACE_WITH_REVIEWER_NAME"
    example = {"document_id": doc.document_id, "base_version": doc.version, "reviewer": reviewer_note,
               "changes": ([{"observation_id": first.id, "value": None, "raw": first.raw, "state": "unreadable",
                             "reason": "REPLACE_WITH_REASON: what you verified in the source page"}]
                           if first else []), "approve": first is None}
    CorrectionSet.model_validate(example)  # the shipped example must itself parse
    bundle = {
        "format": BUNDLE_FORMAT, "document_id": doc.document_id, "version": doc.version,
        "parent_version": doc.parent_version, "snapshot_sha256": snapshot_sha, "doc_type": doc.doc_type,
        "validation": doc.validation, "approval": doc.approval, "state_dir": str(store.state_dir),
        "artifact_root": str(store.artifact_root(job)),
        "source": source,
        "coverage": doc.coverage.model_dump(mode="json"),
        "issues": [{"seq": i, "code": x.code, "message": x.message, "page": x.page,
                    "page_reference": page_ref(x.page) if x.page else None,
                    "observation_ids": x.observation_ids,
                    "observations": [item(observations[o]) for o in x.observation_ids]}
                   for i, x in enumerate(doc.issues)],
        "review_items": [item(observations[i]) for i in flagged],
        "history": [{k: v[k] for k in ("version", "parent_version", "kind", "base_version", "reviewer",
                                        "approval", "validation", "snapshot_sha256", "created_at")}
                    for v in store.versions(doc.document_id)],
        "possible_duplicates": store.duplicates(doc.document_id),
        "corrections": {
            "schema": "corrections.schema.json", "example": "corrections.example.json",
            "apply": f"vision-tables review apply corrections.json --document {doc.document_id}",
            "base_version": doc.version,
            "rules": ["Set base_version to this version; a changed head is rejected as stale_base.",
                      "A change targets an observation_id; value must be null unless state is readable.",
                      "Calculated observations cannot be edited; correct their inputs instead.",
                      "approve=true is a human decision on exactly this version; it fails while blocking issues remain.",
                      "Repeating an identical file is idempotent and returns the version it created."]},
    }
    return {"review.json": (json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(),
            "corrections.schema.json": (json.dumps(CorrectionSet.model_json_schema(), sort_keys=True, indent=2)
                                        + "\n").encode(),
            "corrections.example.json": (json.dumps(example, ensure_ascii=False, sort_keys=True, indent=2)
                                         + "\n").encode()}


# -- atomic publication ---------------------------------------------------------------------
def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_file(path: Path, data: bytes) -> None:
    temp = path.with_name(f".tmp-{uuid.uuid4().hex}")
    with temp.open("wb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)
    _fsync_dir(path.parent)


def _tree_hashes(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): digest_bytes(p.read_bytes())
            for p in sorted(root.rglob("*")) if p.is_file()}


def _publish_dir(target: Path, name: str, files: dict[str, bytes]) -> None:
    final = target / name
    wanted = {n: digest_bytes(b) for n, b in files.items()}
    if final.is_dir() and _tree_hashes(final) == wanted:
        return
    stage = target / f".stage-{uuid.uuid4().hex}"
    stage.mkdir()
    for file_name, data in files.items():
        _write_file(stage / file_name, data)
    old = None
    if final.exists():
        old = target / f".old-{uuid.uuid4().hex}"
        os.rename(final, old)
    os.rename(stage, final)
    _fsync_dir(target)
    if old:
        shutil.rmtree(old, ignore_errors=True)


def _sqlite_bytes(store: Store, document_id: str, version: int, scratch: Path) -> bytes:
    path = scratch / "document.sqlite3"
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        apply_migrations(conn, store.created_at(document_id, version))
        copy_version(store, conn, document_id, version)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return path.read_bytes()


def publish(store: Store, document_id: str, version: int, output_dir: Path | str,
            formats: tuple[str, ...] = DEFAULT_FORMATS) -> dict:
    """Publish selected formats of one snapshot. Idempotent; safe to retry after any interruption."""
    unknown = set(formats) - set(FORMATS)
    if unknown:
        raise ProcessingError("invalid_format", f"Unknown export format(s): {', '.join(sorted(unknown))}.")
    out = Path(output_dir).resolve()
    target = out / document_id / f"v{version:04d}"
    if not target.resolve().is_relative_to(out) or document_id in (".", ".."):
        raise ProcessingError("invalid_input", "Document id cannot be used as a directory name.")
    raw, snapshot_sha = store.snapshot(document_id, version)
    doc = Document.model_validate_json(raw)
    target.mkdir(parents=True, exist_ok=True)
    with (target / ".publish.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for leftover in target.glob(".stage-*"):  # crashed publishers; we hold the lock
            shutil.rmtree(leftover, ignore_errors=True)
        for leftover in (*target.glob(".old-*"), *target.glob(".tmp-*")):
            shutil.rmtree(leftover, ignore_errors=True) if leftover.is_dir() else leftover.unlink(missing_ok=True)
        written: dict[str, bytes] = {}
        if "json" in formats:
            _write_file(target / "document.json", raw)
            written["document.json"] = raw
        if "csv" in formats:
            tables = render_tables(doc)
            _publish_dir(target, "tables", tables)
            written |= {f"tables/{n}": b for n, b in tables.items()}
        if "review" in formats:
            review = review_files(store, doc, snapshot_sha)
            _publish_dir(target, "review", review)
            written |= {f"review/{n}": b for n, b in review.items()}
        if "sqlite" in formats:
            scratch = target / f".stage-{uuid.uuid4().hex}"
            scratch.mkdir()
            try:
                data = _sqlite_bytes(store, document_id, version, scratch)
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
            _write_file(target / "document.sqlite3", data)
            written["document.sqlite3"] = data
        manifest_path = target / "manifest.json"
        previous = {}
        if manifest_path.is_file():
            try:
                previous = json.loads(manifest_path.read_text())
            except ValueError:
                previous = {}
        merged = previous.get("files", {}) if previous.get("snapshot_sha256") == snapshot_sha else {}
        merged = merged | {name: digest_bytes(data) for name, data in written.items()}
        manifest = {"format": MANIFEST_FORMAT, "document_id": document_id, "version": version,
                    "parent_version": doc.parent_version, "snapshot_sha256": snapshot_sha,
                    "validation": doc.validation, "approval": doc.approval,
                    "files": dict(sorted(merged.items()))}
        store.fault("export:before_manifest")
        body = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        _write_file(manifest_path, body)
    manifest_sha = digest_bytes(body)
    store.mark_export(document_id, version, str(out), manifest_sha, tuple(f for f in FORMATS if f in formats))
    return {"directory": str(target), "manifest": str(manifest_path), "manifest_sha256": manifest_sha,
            "snapshot_sha256": snapshot_sha, "files": sorted(merged)}


def publish_pending(store: Store) -> list[dict]:
    """Retry every unfinished publication. Failures stay pending and are reported, never hidden."""
    results = []
    for row in store.pending_exports():
        entry = {"document_id": row["document_id"], "version": row["version"], "output_dir": row["output_dir"]}
        try:
            entry |= {"state": "published"} | publish(store, row["document_id"], row["version"],
                                                       row["output_dir"], tuple(row["formats"].split(",")))
        except (OSError, ProcessingError) as exc:
            entry |= {"state": "pending", "error": str(exc)}
        results.append(entry)
    return results
