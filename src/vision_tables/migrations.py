"""Versioned SQLite migrations embedded as SQL so every installation can find them.

Add new migrations only by appending the next sequential number (coordinator-owned ordering). An
applied migration is checksummed; a database edited out of band or created by a newer program is
refused instead of silently reinterpreted.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from .config import ProcessingError

# Tables whose rows describe an immutable snapshot. Updates and deletes are rejected by trigger.
IMMUTABLE = ("document_versions", "pages", "regions", "entities", "observations",
             "evidence_links", "issues", "corrections")

_V1 = [
    """CREATE TABLE sources (
        sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64),
        media_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, page_count INTEGER NOT NULL,
        first_job_id TEXT NOT NULL, original_path TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE batches (
        batch_id TEXT PRIMARY KEY, input_dir TEXT NOT NULL, output_dir TEXT NOT NULL,
        settings_json TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN
            ('running','completed','incomplete','failed','cancelled')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE jobs (
        job_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('extract','batch_item')),
        input_path TEXT NOT NULL, source_sha256 TEXT, output_dir TEXT NOT NULL,
        settings_json TEXT NOT NULL, artifact_root TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('running','persisting','persisted','completed',
            'incomplete','failed','cancelled','interrupted')),
        error_code TEXT, error_message TEXT, batch_id TEXT REFERENCES batches(batch_id),
        owner_token TEXT, owner_pid INTEGER, owner_host TEXT, lease_expires_at REAL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT)""",
    "CREATE INDEX jobs_state ON jobs(state)",
    """CREATE TABLE batch_items (
        batch_id TEXT NOT NULL REFERENCES batches(batch_id), name TEXT NOT NULL,
        source_sha256 TEXT, state TEXT NOT NULL CHECK (state IN
            ('pending','running','completed','incomplete','failed','skipped','cancelled')),
        job_id TEXT, detail_json TEXT, owner_token TEXT, lease_expires_at REAL,
        updated_at TEXT NOT NULL, PRIMARY KEY (batch_id, name))""",
    """CREATE TABLE documents (
        document_id TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL REFERENCES sources(sha256),
        scope_json TEXT NOT NULL, doc_type TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE document_versions (
        document_id TEXT NOT NULL REFERENCES documents(document_id), version INTEGER NOT NULL,
        parent_version INTEGER, kind TEXT NOT NULL CHECK (kind IN ('extraction','correction')),
        base_version INTEGER NOT NULL, config_hash TEXT NOT NULL, job_id TEXT NOT NULL,
        validation TEXT NOT NULL, approval TEXT NOT NULL, reviewer TEXT,
        snapshot_json TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL, signature_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (document_id, version),
        FOREIGN KEY (document_id, parent_version) REFERENCES document_versions(document_id, version),
        CHECK (version >= 1), CHECK (parent_version IS NULL OR parent_version < version),
        CHECK (kind = 'extraction' OR parent_version IS NOT NULL),
        CHECK (base_version >= 1 AND base_version <= version))""",
    # One base extraction per logical document and effective configuration. 'provisional' marks an
    # incomplete base that a later complete retry under the same key may replace while unedited.
    """CREATE TABLE extractions (
        document_id TEXT NOT NULL, config_hash TEXT NOT NULL, version INTEGER NOT NULL,
        finality TEXT NOT NULL CHECK (finality IN ('final','provisional')),
        PRIMARY KEY (document_id, config_hash),
        FOREIGN KEY (document_id, version) REFERENCES document_versions(document_id, version))""",
    # Identifies a repeated correction operation by canonical payload digest.
    """CREATE TABLE corrections (
        document_id TEXT NOT NULL, op_digest TEXT NOT NULL, base_version INTEGER NOT NULL,
        result_version INTEGER NOT NULL, reviewer TEXT NOT NULL, payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL, PRIMARY KEY (document_id, op_digest),
        UNIQUE (document_id, result_version),
        FOREIGN KEY (document_id, result_version) REFERENCES document_versions(document_id, version))""",
    """CREATE TABLE pages (
        document_id TEXT NOT NULL, version INTEGER NOT NULL, number INTEGER NOT NULL,
        width REAL NOT NULL, height REAL NOT NULL, unit TEXT NOT NULL, image_path TEXT NOT NULL,
        image_width INTEGER NOT NULL, image_height INTEGER NOT NULL, transform_json TEXT NOT NULL,
        text_layer TEXT NOT NULL, job_id TEXT NOT NULL,
        PRIMARY KEY (document_id, version, number),
        FOREIGN KEY (document_id, version) REFERENCES document_versions(document_id, version))""",
    """CREATE TABLE regions (
        document_id TEXT NOT NULL, version INTEGER NOT NULL, id TEXT NOT NULL, page INTEGER NOT NULL,
        ordinal INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, language TEXT,
        writing TEXT NOT NULL, box_json TEXT, artifact_path TEXT, artifact_sha256 TEXT,
        PRIMARY KEY (document_id, version, id),
        FOREIGN KEY (document_id, version) REFERENCES document_versions(document_id, version))""",
    """CREATE TABLE entities (
        document_id TEXT NOT NULL, version INTEGER NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL,
        parent_id TEXT, page INTEGER NOT NULL, ordinal INTEGER NOT NULL,
        PRIMARY KEY (document_id, version, id),
        FOREIGN KEY (document_id, version) REFERENCES document_versions(document_id, version))""",
    """CREATE TABLE observations (
        document_id TEXT NOT NULL, version INTEGER NOT NULL, id TEXT NOT NULL,
        entity_id TEXT NOT NULL, entity_kind TEXT NOT NULL, field TEXT NOT NULL, raw TEXT,
        value TEXT, value_type TEXT NOT NULL CHECK (value_type IN ('null','str','bool')),
        state TEXT NOT NULL, origin TEXT NOT NULL, language TEXT, reason TEXT,
        alternatives_json TEXT NOT NULL, derivation_json TEXT,
        PRIMARY KEY (document_id, version, id),
        UNIQUE (document_id, version, entity_id, field),
        FOREIGN KEY (document_id, version) REFERENCES document_versions(document_id, version))""",
    # Materialized links that resolve each reading to its source file, page image and region box.
    """CREATE TABLE evidence_links (
        document_id TEXT NOT NULL, version INTEGER NOT NULL, observation_id TEXT NOT NULL,
        seq INTEGER NOT NULL, page INTEGER NOT NULL, region_id TEXT, quote TEXT NOT NULL,
        channel TEXT, attempt_id TEXT, source_sha256 TEXT NOT NULL, job_id TEXT NOT NULL,
        source_path TEXT NOT NULL, page_image_path TEXT NOT NULL, region_box_json TEXT,
        PRIMARY KEY (document_id, version, observation_id, seq),
        FOREIGN KEY (document_id, version) REFERENCES document_versions(document_id, version))""",
    """CREATE TABLE issues (
        document_id TEXT NOT NULL, version INTEGER NOT NULL, seq INTEGER NOT NULL,
        code TEXT NOT NULL, message TEXT NOT NULL, page INTEGER, observation_ids_json TEXT NOT NULL,
        PRIMARY KEY (document_id, version, seq),
        FOREIGN KEY (document_id, version) REFERENCES document_versions(document_id, version))""",
    # An export row is pending until its manifest exists, so interrupted publication is recoverable.
    """CREATE TABLE exports (
        document_id TEXT NOT NULL, version INTEGER NOT NULL, output_dir TEXT NOT NULL,
        formats TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN ('pending','published')),
        manifest_sha256 TEXT, updated_at TEXT NOT NULL,
        PRIMARY KEY (document_id, version, output_dir),
        FOREIGN KEY (document_id, version) REFERENCES document_versions(document_id, version))""",
    # Review links only. Nothing is merged or deleted on a possible duplicate.
    """CREATE TABLE duplicate_links (
        document_id TEXT NOT NULL REFERENCES documents(document_id),
        other_document_id TEXT NOT NULL REFERENCES documents(document_id),
        basis TEXT NOT NULL, score REAL NOT NULL, status TEXT NOT NULL DEFAULT 'review',
        created_at TEXT NOT NULL,
        PRIMARY KEY (document_id, other_document_id), CHECK (document_id < other_document_id))""",
]
for _table in IMMUTABLE:
    _V1.append(f"CREATE TRIGGER {_table}_no_update BEFORE UPDATE ON {_table} "
               f"BEGIN SELECT RAISE(ABORT, '{_table} rows are immutable'); END")
    _V1.append(f"CREATE TRIGGER {_table}_no_delete BEFORE DELETE ON {_table} "
               f"BEGIN SELECT RAISE(ABORT, '{_table} rows are immutable'); END")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        return hashlib.sha256("\n;\n".join(self.statements).encode()).hexdigest()


MIGRATIONS = (Migration(1, "initial", tuple(_V1)),)
LATEST = MIGRATIONS[-1].version


def apply_migrations(conn: sqlite3.Connection, now: str, migrations=MIGRATIONS) -> list[int]:
    """Apply pending migrations in one immediate transaction. Safe against concurrent starters."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
                 "name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)")
    conn.execute("BEGIN IMMEDIATE")
    try:
        applied = {r[0]: r[1] for r in conn.execute("SELECT version, checksum FROM schema_migrations")}
        known = {m.version: m for m in migrations}
        if applied.keys() - known.keys():
            raise ProcessingError("database_newer", "The database was created by a newer version "
                                  "of vision-tables. Upgrade the program; the database was not changed.")
        for version, checksum in applied.items():
            if known[version].checksum != checksum:
                raise ProcessingError("migration_mismatch", f"Applied migration {version} differs from the "
                                      "program's definition. Restore the database or program version.")
        done = []
        for migration in migrations:
            if migration.version in applied:
                continue
            for statement in migration.statements:
                conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations VALUES (?,?,?,?)",
                         (migration.version, migration.name, migration.checksum, now))
            done.append(migration.version)
        conn.execute("COMMIT")
        return done
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
