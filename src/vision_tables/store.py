"""Durable SQLite store: jobs, immutable document versions, replay and correction history.

Guarantees (each enforced inside one BEGIN IMMEDIATE transaction):

* Version numbers are allocated per document under the write lock and are immutable once inserted.
* A base extraction is keyed on (document_id, config_hash). Reimport returns the reviewed descendant
  of that base and never adds a second accepted record. A changed configuration is a new extraction
  version linked to the prior one; the older snapshots stay.
* A correction is identified by the canonical digest of its payload. A replay is answered before the
  stale-base check; otherwise the base must still be the head of its lineage when the child is inserted.
* Snapshot rows (versions, pages, regions, entities, observations, evidence links, issues) are
  rejected by trigger if updated or deleted.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import time
import unicodedata
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from jsonschema import Draft202012Validator

from .config import ProcessingError, digest
from .contracts import CorrectionSet, Document
from .migrations import apply_migrations

DB_NAME = "vision-tables.sqlite3"
LEASE_SECONDS = 120.0
DEFAULT_FORMATS = ("json", "csv", "review")
_SCHEMA_VALIDATOR: Draft202012Validator | None = None


def now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def canonical_bytes(document: Document) -> bytes:
    data = document.model_dump(mode="json")
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def check_document(document: Document) -> Document:
    """Revalidate with the strict model and the published JSON Schema at the persistence boundary."""
    global _SCHEMA_VALIDATOR
    data = document.model_dump(mode="json")
    checked = Document.model_validate(data)
    _SCHEMA_VALIDATOR = _SCHEMA_VALIDATOR or Draft202012Validator(Document.model_json_schema())
    _SCHEMA_VALIDATOR.validate(checked.model_dump(mode="json"))
    return checked


def _fold(text: object) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).casefold()
    return re.sub(r"[^0-9a-z]+", " ", "".join(c for c in text if not unicodedata.combining(c))).strip()


_IDENTITY = {"invoice": ("invoice", ("supplier_name", "document_number")),
             "menu": ("menu", ("venue_name",)), "recipe": ("recipe", ("title",))}
_NAMES = {"invoice": ("line_item", "description"), "menu": ("menu_item", "name"),
          "recipe": ("ingredient", "name")}


def signature(document: Document) -> dict:
    """Normalized identity fields and row names used only to propose possible duplicates."""
    kinds = {e.id: e.kind for e in document.entities}
    identity, names = {}, set()
    root, fields = _IDENTITY.get(document.doc_type, (None, ()))
    name_kind, name_field = _NAMES.get(document.doc_type, (None, None))
    for obs in document.observations:
        if obs.state != "readable":
            continue
        text = _fold(obs.value if obs.value is not None else obs.raw)
        if not text:
            continue
        if kinds[obs.entity_id] == root and obs.field in fields:
            identity[obs.field] = text
        elif kinds[obs.entity_id] == name_kind and obs.field == name_field:
            names.add(text)
    return {"doc_type": document.doc_type, "identity": identity, "names": sorted(names)}


def duplicate_basis(a: dict, b: dict) -> tuple[str, float] | None:
    if a["doc_type"] != b["doc_type"] or a["doc_type"] == "unknown":
        return None
    shared = a["identity"].keys() & b["identity"].keys()
    if any(a["identity"][k] != b["identity"][k] for k in shared):
        return None
    need = len(_IDENTITY[a["doc_type"]][1])
    if len(shared) == need and a["doc_type"] == "invoice":
        return "identical supplier and document number", 1.0
    na, nb = set(a["names"]), set(b["names"])
    if min(len(na), len(nb)) >= 3:
        score = len(na & nb) / len(na | nb)
        if score >= 0.8:
            return "row names overlap (Jaccard >= 0.8)", round(score, 3)
    return None


@dataclass
class Job:
    job_id: str
    token: str
    artifact_root: Path


@dataclass
class Persisted:
    document_id: str
    version: int
    base_version: int
    parent_version: int | None
    action: str  # created | new_extraction | superseded | reused
    created_version: int | None
    document: Document
    possible_duplicates: list[dict] = field(default_factory=list)


@dataclass
class CorrectionResult:
    document_id: str
    version: int
    base_version: int
    replayed: bool
    document: Document
    output_dirs: list[str] = field(default_factory=list)


class Store:
    """Thin, explicit data layer. Every statement is parameterized; a connection is opened per call."""

    fault_hook: Callable[[str], None] | None = None  # interruption tests only

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir).resolve()
        self.path = self.state_dir / DB_NAME
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        conn = self._open()
        try:
            apply_migrations(conn, now_text())
        finally:
            conn.close()

    # -- plumbing ---------------------------------------------------------------------------
    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=60, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=60000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def fault(self, name: str) -> None:
        if self.fault_hook:
            self.fault_hook(name)

    @contextmanager
    def read(self):
        conn = self._open()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def tx(self, name: str = "tx"):
        conn = self._open()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            self.fault(f"{name}:before_commit")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        else:
            self.fault(f"{name}:after_commit")
        finally:
            conn.close()

    def artifact_root(self, job_id: str) -> Path:
        return self.state_dir / "jobs" / job_id

    def resolve_artifact(self, job_id: str, relative: str | None) -> Path | None:
        """Resolve a job-relative artifact path; anything leaving the job root is rejected."""
        if not relative:
            return None
        root = self.artifact_root(job_id).resolve()
        try:
            target = (root / relative).resolve()
        except (OSError, ValueError):
            return None
        return target if target.is_relative_to(root) else None

    # -- jobs -------------------------------------------------------------------------------
    def create_job(self, *, kind: str, input_path: str, output_dir: str, settings_json: str,
                   batch_id: str | None = None) -> Job:
        job_id, token = uuid.uuid4().hex, uuid.uuid4().hex
        now = now_text()
        with self.tx("job") as conn:
            conn.execute(
                "INSERT INTO jobs (job_id, kind, input_path, output_dir, settings_json, artifact_root, state,"
                " batch_id, owner_token, owner_pid, owner_host, lease_expires_at, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?, 'running', ?,?,?,?,?,?,?)",
                (job_id, kind, input_path, output_dir, settings_json, f"jobs/{job_id}", batch_id, token,
                 os.getpid(), socket.gethostname(), time.time() + LEASE_SECONDS, now, now))
        return Job(job_id, token, self.artifact_root(job_id))

    def get_job(self, job_id: str) -> dict | None:
        with self.read() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, state: str | None = None) -> list[dict]:
        with self.read() as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE (?1 IS NULL OR state=?1) ORDER BY created_at, job_id",
                                (state,)).fetchall()
        return [dict(r) for r in rows]

    def heartbeat(self, job_id: str, token: str, item: tuple[str, str] | None = None) -> bool:
        """Extend the lease. False means another process took the job over."""
        until = time.time() + LEASE_SECONDS
        with self.tx("heartbeat") as conn:
            moved = conn.execute("UPDATE jobs SET lease_expires_at=?, updated_at=? WHERE job_id=? AND owner_token=?"
                                 " AND state IN ('running','persisting','persisted')", (until, now_text(), job_id, token)).rowcount
            if item and moved:
                conn.execute("UPDATE batch_items SET lease_expires_at=? WHERE batch_id=? AND name=?"
                             " AND owner_token=?", (until, *item, token))
        return bool(moved)

    def set_job(self, job_id: str, token: str | None, state: str, *, error_code: str | None = None,
                error_message: str | None = None, source_sha256: str | None = None,
                require_owner: bool = True) -> None:
        terminal = state in ("completed", "incomplete", "failed", "cancelled", "interrupted")
        with self.tx("job") as conn:
            self._set_job(conn, job_id, token, state, error_code, error_message, source_sha256, terminal,
                          require_owner)

    def _set_job(self, conn, job_id, token, state, error_code, error_message, source_sha256, terminal,
                 require_owner=True):
        now = now_text()
        params = [state, error_code, error_message, source_sha256, now, now if terminal else None, job_id]
        sql = ("UPDATE jobs SET state=?, error_code=?, error_message=?, source_sha256=COALESCE(?, source_sha256),"
               " updated_at=?, finished_at=?" + (", owner_token=NULL, lease_expires_at=NULL" if terminal else "") +
               " WHERE job_id=? ")
        if require_owner:
            sql += "AND owner_token=?"
            params.append(token)
        if conn.execute(sql, params).rowcount != 1:
            raise ProcessingError("lease_lost", "Another process owns this job now; nothing was changed.")

    def reap_dead_owners(self) -> int:
        """Expire leases held by processes that no longer exist on this host (crash, kill -9)."""
        host, reaped = socket.gethostname(), 0
        with self.tx("reap") as conn:
            rows = conn.execute("SELECT job_id, owner_pid FROM jobs WHERE state IN ('running','persisting',"
                                "'persisted') AND owner_host=? AND lease_expires_at > ?", (host, time.time())).fetchall()
            for row in rows:
                if row["owner_pid"] != os.getpid() and not _alive(row["owner_pid"]):
                    conn.execute("UPDATE jobs SET lease_expires_at=0 WHERE job_id=?", (row["job_id"],))
                    conn.execute("UPDATE batch_items SET lease_expires_at=0 WHERE job_id=? AND state='running'",
                                 (row["job_id"],))
                    reaped += 1
        return reaped

    def claim_expired_jobs(self, only: str | None = None) -> list[tuple[dict, str]]:
        """Take over jobs whose owner stopped renewing the lease. Compare-and-swap per row."""
        claimed = []
        with self.tx("claim") as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE state IN ('running','persisting','persisted')"
                                " AND lease_expires_at < ? AND (?2 IS NULL OR job_id=?2)",
                                (time.time(), only)).fetchall()
            for row in rows:
                token = uuid.uuid4().hex
                if conn.execute("UPDATE jobs SET owner_token=?, owner_pid=?, owner_host=?, lease_expires_at=?"
                                " WHERE job_id=? AND owner_token IS ? AND lease_expires_at < ?",
                                (token, os.getpid(), socket.gethostname(), time.time() + LEASE_SECONDS,
                                 row["job_id"], row["owner_token"], time.time())).rowcount:
                    claimed.append((dict(row), token))
        return claimed

    def claim_job(self, job_id: str) -> str | None:
        """Claim a finished-but-unpersisted job (cancelled or interrupted) for explicit recovery."""
        token = uuid.uuid4().hex
        with self.tx("claim") as conn:
            moved = conn.execute("UPDATE jobs SET state='persisting', owner_token=?, owner_pid=?, owner_host=?,"
                                 " lease_expires_at=?, finished_at=NULL WHERE job_id=?"
                                 " AND state IN ('cancelled','interrupted','failed','running','persisting','persisted')"
                                 " AND (owner_token IS NULL OR lease_expires_at < ?)",
                                 (token, os.getpid(), socket.gethostname(), time.time() + LEASE_SECONDS, job_id,
                                  time.time())).rowcount
        return token if moved else None

    # -- batches ----------------------------------------------------------------------------
    def ensure_batch(self, batch_id: str, input_dir: str, output_dir: str, settings_json: str,
                     names: list[str]) -> None:
        now = now_text()
        with self.tx("batch") as conn:
            conn.execute("INSERT OR IGNORE INTO batches VALUES (?,?,?,?, 'running', ?,?)",
                         (batch_id, input_dir, output_dir, settings_json, now, now))
            conn.execute("UPDATE batches SET state='running', updated_at=? WHERE batch_id=?", (now, batch_id))
            conn.executemany("INSERT OR IGNORE INTO batch_items (batch_id, name, state, updated_at)"
                             " VALUES (?,?, 'pending', ?)", [(batch_id, n, now) for n in names])

    def claim_item(self, batch_id: str, name: str, retry: tuple[str, ...]) -> str | None:
        token, now = uuid.uuid4().hex, time.time()
        marks = ",".join("?" * len(retry))
        with self.tx("claim") as conn:
            moved = conn.execute(
                f"UPDATE batch_items SET state='running', owner_token=?, lease_expires_at=?, updated_at=?"
                f" WHERE batch_id=? AND name=? AND (state IN ('pending'{',' + marks if retry else ''})"
                f" OR (state='running' AND lease_expires_at < ?))",
                (token, now + LEASE_SECONDS, now_text(), batch_id, name, *retry, now)).rowcount
        return token if moved else None

    def finish_item(self, batch_id: str, name: str, token: str, state: str, *, job_id: str | None,
                    detail: dict, source_sha256: str | None) -> None:
        with self.tx("item") as conn:
            conn.execute("UPDATE batch_items SET state=?, job_id=?, detail_json=?, source_sha256=?,"
                         " owner_token=NULL, lease_expires_at=NULL, updated_at=? WHERE batch_id=? AND name=?"
                         " AND owner_token=?", (state, job_id, json.dumps(detail, sort_keys=True),
                                                source_sha256, now_text(), batch_id, name, token))

    def release_item(self, batch_id: str, name: str, token: str, state: str = "pending") -> None:
        self.finish_item(batch_id, name, token, state, job_id=None, detail={}, source_sha256=None)

    def item_job(self, batch_id: str, name: str, token: str, job_id: str) -> None:
        with self.tx("item") as conn:
            conn.execute("UPDATE batch_items SET job_id=? WHERE batch_id=? AND name=? AND owner_token=?",
                         (job_id, batch_id, name, token))

    def batch_items(self, batch_id: str) -> list[dict]:
        with self.read() as conn:
            rows = conn.execute("SELECT * FROM batch_items WHERE batch_id=? ORDER BY name", (batch_id,)).fetchall()
        return [dict(r) for r in rows]

    def set_batch(self, batch_id: str, state: str) -> None:
        with self.tx("batch") as conn:
            conn.execute("UPDATE batches SET state=?, updated_at=? WHERE batch_id=?", (state, now_text(), batch_id))

    # -- persistence of extraction candidates -----------------------------------------------
    def persist_candidates(self, job_id: str, token: str, documents: list[Document]) -> list[Persisted]:
        """Atomically store every candidate of a job. Idempotent for the same job and candidates."""
        checked = [check_document(d) for d in documents]
        if any(d.version != 1 or d.parent_version is not None or d.corrections or d.approval != "needs_review"
               for d in checked):
            raise ProcessingError("invalid_candidate", "Extraction candidates must be fresh unreviewed version 1.")
        results = []
        with self.tx("persist") as conn:
            job = conn.execute("SELECT * FROM jobs WHERE job_id=? AND owner_token=?", (job_id, token)).fetchone()
            if job is None or job["state"] not in ("running", "persisting", "persisted"):
                raise ProcessingError("lease_lost", "This job is not owned by this process; nothing was stored.")
            for doc in checked:
                if doc.run.job_id != job_id:
                    raise ProcessingError("invalid_candidate", "Candidate belongs to a different job.")
                results.append(self._persist_one(conn, doc))
            outputs = [(r.document_id, r.version) for r in results]
            for document_id, version in outputs:
                conn.execute("INSERT INTO exports VALUES (?,?,?,?, 'pending', NULL, ?) ON CONFLICT(document_id,"
                             " version, output_dir) DO UPDATE SET formats=excluded.formats, state='pending',"
                             " manifest_sha256=NULL, updated_at=excluded.updated_at",
                             (document_id, version, job["output_dir"], ",".join(DEFAULT_FORMATS), now_text()))
            self._set_job(conn, job_id, token, "persisted", None, None, checked[0].source.sha256, False)
        return results

    def _persist_one(self, conn, doc: Document) -> Persisted:
        did = doc.document_id
        known = conn.execute("SELECT 1 FROM documents WHERE document_id=?", (did,)).fetchone() is not None
        row = conn.execute("SELECT version, finality FROM extractions WHERE document_id=? AND config_hash=?",
                           (did, doc.run.config_hash)).fetchone()
        action = "new_extraction" if known else "created"
        if row:
            base = row["version"]
            head = self._head(conn, did, base)
            retry_better = row["finality"] == "provisional" and doc.validation != "incomplete" and head == base
            if not retry_better:
                served = self._load(conn, did, head)
                return Persisted(did, head, base, served.parent_version, "reused", None, served,
                                 self._duplicates(conn, did))
            action = "superseded"
        latest = conn.execute("SELECT MAX(version) FROM document_versions WHERE document_id=?", (did,)).fetchone()[0]
        number = (latest or 0) + 1
        stamped = check_document(self._stamp(doc, number, latest))
        now = now_text()
        src = stamped.source
        conn.execute("INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?,?,?)",
                     (src.sha256, src.media_type, src.size_bytes, src.page_count, stamped.run.job_id,
                      src.original_path, now))
        conn.execute("INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?)",
                     (did, src.sha256, json.dumps(stamped.scope), stamped.doc_type, now))
        self._insert_version(conn, stamped, "extraction", number, None, now)
        finality = "provisional" if stamped.validation == "incomplete" else "final"
        conn.execute("INSERT INTO extractions VALUES (?,?,?,?) ON CONFLICT(document_id, config_hash)"
                     " DO UPDATE SET version=excluded.version, finality=excluded.finality",
                     (did, stamped.run.config_hash, number, finality))
        self._link_duplicates(conn, stamped, now)
        return Persisted(did, number, number, latest, action, number, stamped, self._duplicates(conn, did))

    @staticmethod
    def _stamp(doc: Document, version: int, parent: int | None) -> Document:
        data = doc.model_dump(mode="json")
        data.update(version=version, parent_version=parent)
        return Document.model_validate(data)

    def _insert_version(self, conn, doc: Document, kind: str, base_version: int, reviewer: str | None, now: str):
        payload = canonical_bytes(doc)
        conn.execute("INSERT INTO document_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (doc.document_id, doc.version, doc.parent_version, kind, base_version, doc.run.config_hash,
                      doc.run.job_id, doc.validation, doc.approval, reviewer, payload.decode(),
                      digest_bytes(payload), json.dumps(signature(doc), sort_keys=True), now))
        materialize(conn, doc)

    def _link_duplicates(self, conn, doc: Document, now: str) -> None:
        mine = signature(doc)
        rows = conn.execute(
            "SELECT v.document_id, v.signature_json FROM document_versions v JOIN documents d USING (document_id)"
            " WHERE d.doc_type=? AND d.source_sha256 != ? AND v.document_id != ? AND v.version ="
            " (SELECT MAX(version) FROM document_versions WHERE document_id=v.document_id)",
            (doc.doc_type, doc.source.sha256, doc.document_id)).fetchall()
        for row in rows:
            found = duplicate_basis(mine, json.loads(row["signature_json"]))
            if found:
                a, b = sorted((doc.document_id, row["document_id"]))
                conn.execute("INSERT OR IGNORE INTO duplicate_links VALUES (?,?,?,?, 'review', ?)",
                             (a, b, found[0], found[1], now))

    @staticmethod
    def _duplicates(conn, document_id: str) -> list[dict]:
        rows = conn.execute("SELECT * FROM duplicate_links WHERE document_id=?1 OR other_document_id=?1"
                            " ORDER BY document_id, other_document_id", (document_id,)).fetchall()
        return [{"document_id": r["other_document_id"] if r["document_id"] == document_id else r["document_id"],
                 "basis": r["basis"], "score": r["score"], "status": r["status"]} for r in rows]

    @staticmethod
    def _head(conn, document_id: str, base_version: int) -> int:
        return conn.execute("SELECT MAX(version) FROM document_versions WHERE document_id=? AND base_version=?",
                            (document_id, base_version)).fetchone()[0]

    @staticmethod
    def _load(conn, document_id: str, version: int) -> Document:
        row = conn.execute("SELECT snapshot_json, snapshot_sha256 FROM document_versions WHERE document_id=?"
                           " AND version=?", (document_id, version)).fetchone()
        if row is None:
            raise ProcessingError("unknown_version", f"Document {document_id} has no version {version}.")
        if digest_bytes(row["snapshot_json"].encode()) != row["snapshot_sha256"]:
            raise ProcessingError("integrity_error", f"Stored snapshot {document_id} v{version} was altered.")
        return Document.model_validate_json(row["snapshot_json"])

    # -- corrections ------------------------------------------------------------------------
    def apply_corrections(self, document_id: str, changes: CorrectionSet,
                          apply: Callable[[Document, CorrectionSet], Document] | None = None,
                          extra_outputs: tuple[str, ...] = ()) -> CorrectionResult:
        """Replay check, lineage head check, domain recomputation and insert in one transaction."""
        if changes.document_id != document_id:
            raise ProcessingError("document_mismatch", "The corrections file names a different document.")
        if apply is None:
            apply = default_apply()
        op = digest(changes.model_dump(mode="json"))
        with self.tx("correct") as conn:
            done = conn.execute("SELECT result_version, base_version FROM corrections WHERE document_id=?"
                                " AND op_digest=?", (document_id, op)).fetchone()
            if done:
                outputs = self._pending_outputs(conn, document_id, done["result_version"], extra_outputs)
                return CorrectionResult(document_id, done["result_version"], done["base_version"], True,
                                        self._load(conn, document_id, done["result_version"]), outputs)
            base_row = conn.execute("SELECT base_version FROM document_versions WHERE document_id=? AND version=?",
                                    (document_id, changes.base_version)).fetchone()
            if base_row is None:
                raise ProcessingError("unknown_version", f"Document {document_id} has no version "
                                      f"{changes.base_version}; list versions with 'documents show'.")
            head = self._head(conn, document_id, base_row["base_version"])
            if head != changes.base_version:
                raise ProcessingError("stale_base", f"Base version {changes.base_version} is no longer the latest of "
                                      f"its lineage (current version {head}). Re-export the current version and "
                                      "re-review; nothing was applied.")
            base = self._load(conn, document_id, changes.base_version)
            try:
                result = apply(base, changes)
            except ProcessingError:
                raise
            except ValueError as exc:  # includes pydantic ValidationError
                raise ProcessingError("invalid_correction", f"Corrections were rejected; nothing applied: {exc}") from exc
            if (result.document_id != document_id or result.source.sha256 != base.source.sha256
                    or not result.corrections or result.corrections[-1] != changes):
                raise ProcessingError("invalid_correction", "The domain result does not record this decision.")
            latest = conn.execute("SELECT MAX(version) FROM document_versions WHERE document_id=?",
                                  (document_id,)).fetchone()[0]
            number = latest + 1
            stamped = check_document(self._stamp(result, number, base.version))
            self._insert_version(conn, stamped, "correction", base_row["base_version"], changes.reviewer, now_text())
            conn.execute("INSERT INTO corrections VALUES (?,?,?,?,?,?,?)",
                         (document_id, op, base.version, number, changes.reviewer,
                          json.dumps(changes.model_dump(mode="json"), sort_keys=True, ensure_ascii=False), now_text()))
            outputs = self._pending_outputs(conn, document_id, number, extra_outputs)
            return CorrectionResult(document_id, number, base.version, False, stamped, outputs)

    def _pending_outputs(self, conn, document_id: str, version: int, extra: tuple[str, ...]) -> list[str]:
        """Regenerate every existing export location of this document, plus the requested one."""
        dirs = {r[0] for r in conn.execute("SELECT DISTINCT output_dir FROM exports WHERE document_id=?",
                                           (document_id,)) if Path(r[0]).is_dir()}
        dirs |= set(extra)
        for out in sorted(dirs):
            conn.execute("INSERT OR IGNORE INTO exports VALUES (?,?,?,?, 'pending', NULL, ?)",
                         (document_id, version, out, ",".join(DEFAULT_FORMATS), now_text()))
        return sorted(dirs)

    # -- reads ------------------------------------------------------------------------------
    def get_document(self, document_id: str, version: int | None = None) -> Document:
        with self.read() as conn:
            if version is None:
                version = conn.execute("SELECT MAX(version) FROM document_versions WHERE document_id=?",
                                       (document_id,)).fetchone()[0]
                if version is None:
                    raise ProcessingError("unknown_document", f"No document {document_id} in this state directory.")
            return self._load(conn, document_id, version)

    def versions(self, document_id: str) -> list[dict]:
        with self.read() as conn:
            rows = conn.execute("SELECT version, parent_version, kind, base_version, config_hash, job_id, validation,"
                                " approval, reviewer, snapshot_sha256, created_at FROM document_versions"
                                " WHERE document_id=? ORDER BY version", (document_id,)).fetchall()
        return [dict(r) for r in rows]

    def snapshot(self, document_id: str, version: int) -> tuple[bytes, str]:
        with self.read() as conn:
            self._load(conn, document_id, version)
            row = conn.execute("SELECT snapshot_json, snapshot_sha256, created_at FROM document_versions WHERE"
                               " document_id=? AND version=?", (document_id, version)).fetchone()
        return row["snapshot_json"].encode(), row["snapshot_sha256"]

    def created_at(self, document_id: str, version: int) -> str:
        with self.read() as conn:
            return conn.execute("SELECT created_at FROM document_versions WHERE document_id=? AND version=?",
                                (document_id, version)).fetchone()[0]

    def list_documents(self) -> list[dict]:
        with self.read() as conn:
            rows = conn.execute(
                "SELECT d.document_id, d.doc_type, d.source_sha256, d.scope_json, v.version, v.validation,"
                " v.approval FROM documents d JOIN document_versions v ON v.document_id=d.document_id AND"
                " v.version=(SELECT MAX(version) FROM document_versions WHERE document_id=d.document_id)"
                " ORDER BY d.document_id").fetchall()
        return [dict(r) | {"scope": json.loads(r["scope_json"])} for r in rows]

    def duplicates(self, document_id: str) -> list[dict]:
        with self.read() as conn:
            return self._duplicates(conn, document_id)

    # -- export bookkeeping -----------------------------------------------------------------
    def pending_exports(self) -> list[dict]:
        with self.read() as conn:
            rows = conn.execute("SELECT * FROM exports WHERE state='pending' ORDER BY document_id, version, "
                                "output_dir").fetchall()
        return [dict(r) for r in rows]

    def register_export(self, document_id: str, version: int, output_dir: str, formats: tuple[str, ...]) -> None:
        with self.tx("export") as conn:
            row = conn.execute("SELECT formats FROM exports WHERE document_id=? AND version=? AND output_dir=?",
                               (document_id, version, output_dir)).fetchone()
            merged = sorted(set(formats) | (set(row["formats"].split(",")) if row else set()))
            conn.execute("INSERT INTO exports VALUES (?,?,?,?, 'pending', NULL, ?) ON CONFLICT(document_id, version,"
                         " output_dir) DO UPDATE SET formats=excluded.formats, state='pending',"
                         " manifest_sha256=NULL, updated_at=excluded.updated_at",
                         (document_id, version, output_dir, ",".join(merged), now_text()))

    def release(self, job_id: str, token: str) -> None:
        """Mark a still-unfinished job's lease expired so 'jobs recover' can take it over at once."""
        with self.tx("job") as conn:
            conn.execute("UPDATE jobs SET lease_expires_at=0 WHERE job_id=? AND owner_token=?", (job_id, token))

    def mark_export(self, document_id: str, version: int, output_dir: str, manifest_sha256: str) -> None:
        with self.tx("export") as conn:
            conn.execute("UPDATE exports SET state='published', manifest_sha256=?, updated_at=? WHERE document_id=?"
                         " AND version=? AND output_dir=?", (manifest_sha256, now_text(), document_id, version,
                                                             output_dir))


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def digest_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def default_apply() -> Callable[[Document, CorrectionSet], Document]:
    try:
        from . import domain
    except ImportError as exc:
        raise ProcessingError("domain_unavailable", "The domain module is not installed in this checkout; "
                              "corrections cannot be validated.") from exc
    return domain.apply_corrections


def _scalar(value) -> tuple[str | None, str]:
    if value is None:
        return None, "null"
    if isinstance(value, bool):
        return ("true" if value else "false"), "bool"
    return value, "str"


def materialize(conn: sqlite3.Connection, doc: Document) -> None:
    """Write the relational rows of one snapshot. The same function feeds the standalone SQLite export."""
    did, ver, job = doc.document_id, doc.version, doc.run.job_id
    pages = {p.number: p for p in doc.pages}
    regions = {r.id: r for r in doc.regions}
    kinds = {e.id: e.kind for e in doc.entities}
    conn.executemany("INSERT INTO pages VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
        (did, ver, p.number, p.width, p.height, p.unit, p.image_path, p.image_width, p.image_height,
         json.dumps(p.image_to_original), p.text_layer, job) for p in doc.pages])
    conn.executemany("INSERT INTO regions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
        (did, ver, r.id, r.page, r.order, r.kind, r.text, r.language, r.writing,
         json.dumps(r.box.model_dump(mode="json"), sort_keys=True) if r.box else None, r.artifact_path,
         r.artifact_sha256) for r in doc.regions])
    conn.executemany("INSERT INTO entities VALUES (?,?,?,?,?,?,?)", [
        (did, ver, e.id, e.kind, e.parent_id, e.page, e.ordinal) for e in doc.entities])
    for obs in doc.observations:
        value, value_type = _scalar(obs.value)
        conn.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (did, ver, obs.id, obs.entity_id, kinds[obs.entity_id], obs.field, obs.raw, value, value_type,
                      obs.state, obs.origin, obs.language, obs.reason, json.dumps(obs.alternatives, ensure_ascii=False),
                      json.dumps(obs.derivation.model_dump(mode="json"), sort_keys=True) if obs.derivation else None))
        for seq, ref in enumerate(obs.evidence):
            region = regions.get(ref.region_id) if ref.region_id else None
            conn.execute("INSERT INTO evidence_links VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (did, ver, obs.id, seq, ref.page, ref.region_id, ref.quote, ref.channel, ref.attempt_id,
                          doc.source.sha256, job, doc.source.original_path, pages[ref.page].image_path,
                          json.dumps(region.box.model_dump(mode="json"), sort_keys=True)
                          if region and region.box else None))
    conn.executemany("INSERT INTO issues VALUES (?,?,?,?,?,?,?)", [
        (did, ver, i, issue.code, issue.message, issue.page, json.dumps(issue.observation_ids))
        for i, issue in enumerate(doc.issues)])


def copy_version(store: Store, destination: sqlite3.Connection, document_id: str, version: int) -> None:
    """Copy one snapshot and its materialized rows into a standalone database (schema already applied)."""
    with store.read() as conn:
        row = conn.execute("SELECT * FROM document_versions WHERE document_id=? AND version=?",
                           (document_id, version)).fetchone()
        doc_row = conn.execute("SELECT * FROM documents WHERE document_id=?", (document_id,)).fetchone()
        src_row = conn.execute("SELECT * FROM sources WHERE sha256=?", (doc_row["source_sha256"],)).fetchone()
    document = Document.model_validate_json(row["snapshot_json"])
    destination.execute("BEGIN IMMEDIATE")
    try:
        for table, data in (("sources", src_row), ("documents", doc_row), ("document_versions", row)):
            destination.execute(f"INSERT INTO {table} VALUES ({','.join('?' * len(data.keys()))})", tuple(data))
        materialize(destination, document)
        destination.execute("COMMIT")
    except BaseException:
        if destination.in_transaction:
            destination.execute("ROLLBACK")
        raise
