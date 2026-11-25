"""E1 recheck: frozen-code normal CLI evidence for the release invoice (no inference, read-only).

Run from the repository root:
    .venv/bin/python -I evidence/development/reviewer/release_e1_check.py [STATE_DIR]

STATE_DIR defaults to the main checkout's .vt-state. It is opened read-only (SQLite mode=ro); job artifacts
are only read. Model environments are queried for package versions only, to recompute the run fingerprint.
"""
import csv
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))
from vision_tables import models  # noqa: E402
from vision_tables.config import Settings  # noqa: E402
from vision_tables.contracts import Document  # noqa: E402
from vision_tables.pipeline import extraction_fingerprint  # noqa: E402

STATE = Path(sys.argv[1] if len(sys.argv) > 1 else "/Users/vakof/orca/vision-to-tables/.vt-state")
BASE = Path("evidence/examples/release-invoice")
DOC = "doc-5f704466a44dd696b8317362adec39ec"
verification = json.loads((BASE / "verification.json").read_text())
source_sha = hashlib.sha256(Path(verification["source"]).read_bytes()).hexdigest()
print("source sha256 file/receipt match:", source_sha == verification["source_sha256"])
guards = [s["guard"] for s in verification["initial_extractions"]] + [c["guard"] for c in verification["correction_workflow"]]
print("guards:", len(guards), "all loopback ok and EPERM:", all(g == {"loopback": True, "public_connect_errno": 1} for g in guards))
print("exit codes:", [s["exit_code"] for s in verification["initial_extractions"]],
      [(c["status"], c["exit_code"]) for c in verification["correction_workflow"]])

docs = {}
for vdir in sorted((BASE / DOC).iterdir()):
    manifest = json.loads((vdir / "manifest.json").read_text())
    raw = (vdir / "document.json").read_bytes()
    doc = Document.model_validate_json(raw)
    docs[doc.version] = doc
    files_ok = all(hashlib.sha256((vdir / n).read_bytes()).hexdigest() == d for n, d in manifest["files"].items())
    with (vdir / "tables" / "observations.csv").open(newline="") as fp:
        table = {r["observation_id"]: r for r in csv.DictReader(fp)}
    conn = sqlite3.connect(f"file:{vdir / 'document.sqlite3'}?mode=ro", uri=True)
    sql = {r[0]: r for r in conn.execute("SELECT id, value, state, origin FROM observations")}
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    agree = (set(table) == set(sql) == {o.id for o in doc.observations} and all(
        sql[o.id][2] == o.state == table[o.id]["state"] and sql[o.id][3] == o.origin == table[o.id]["origin"]
        and (sql[o.id][1] or "") == (table[o.id]["value"] or "") == (o.value or "") for o in doc.observations))
    print(f"v{doc.version}: manifest_ok={files_ok} snapshot_sha={hashlib.sha256(raw).hexdigest()[:12]} "
          f"(receipt {next(s['sha256'] for s in verification['snapshots'] if s['version'] == doc.version)[:12]}) "
          f"json_csv_sqlite_agree={agree} sqlite={integrity} fk={len(fk)} parent={doc.parent_version} "
          f"pipeline={doc.run.pipeline} pipeline_version={doc.run.pipeline_version} job={doc.run.job_id[:8]} "
          f"source_ok={doc.source.sha256 == source_sha} approval={doc.approval} validation={doc.validation}")
    print("    ", {o.field: (o.raw, o.value, o.origin) for o in doc.observations})

# Fingerprint: recompute from the frozen code and the installed runtime (version queries only, no inference).
for version in (4, 5):
    doc = docs[version]
    settings = Settings(state_dir=STATE, **{k: v for k, v in doc.run.settings.items()
                                            if k not in ("page_groups",)}, page_groups=tuple(map(tuple, doc.run.settings["page_groups"])))
    info = models.runtime_info(settings)
    print(f"v{version} config_hash matches recomputed fingerprint: "
          f"{extraction_fingerprint(settings, info) == doc.run.config_hash} (ready={info['ready']}, "
          f"model={doc.run.model} rev={doc.run.revision[:12]} max_tokens={doc.run.settings['max_tokens']})")

# Observations against the job's local attempt artifacts.
for version in (4, 5):
    doc = docs[version]
    root = STATE / "jobs" / doc.run.job_id
    job = json.loads((root / "job.json").read_text())
    original = hashlib.sha256((root / "original").read_bytes()).hexdigest()
    replies = {}
    for attempt in sorted((root / "attempts").iterdir()):
        reply = json.loads((attempt / "reply.json").read_text())
        replies[attempt.name] = reply
    qwen_text = " ".join(r.get("raw", "") for k, r in replies.items() if "qwen" in k)
    calls = {c["attempt_id"] for c in doc.run.calls}
    checks = []
    for o in doc.observations:
        if o.origin != "observed":
            continue
        for ref in o.evidence:
            checks.append(ref.attempt_id in calls and ref.attempt_id in replies
                          and json.dumps(o.raw, ensure_ascii=False)[1:-1] in qwen_text
                          and (not ref.quote or json.dumps(ref.quote, ensure_ascii=False)[1:-1] in qwen_text))
    print(f"v{version} job {doc.run.job_id[:8]}: job_status={job.get('status')} original_sha_ok={original == source_sha} "
          f"attempts={sorted(replies)} observed_raw_and_quotes_in_attempt_replies={all(checks)} ({len(checks)} refs) "
          f"finish={[(k, r.get('finish_reason'), r.get('generated_tokens')) for k, r in replies.items() if 'qwen' in k]}")

# Store state: correction, replay and same-configuration reimport (read-only).
db = sqlite3.connect(f"file:{STATE / 'vision-tables.sqlite3'}?mode=ro", uri=True)
db.row_factory = sqlite3.Row
corrections = db.execute("SELECT base_version, result_version FROM corrections WHERE document_id=?", (DOC,)).fetchall()
print("correction rows (base->result):", [(r[0], r[1]) for r in corrections])
v6 = docs[6]
print("v6 human change:", [(o.field, o.value, o.origin) for o in v6.observations if o.origin == "human_supplied"],
      "approve flags:", [c.approve for c in v6.corrections], "approval:", v6.approval)
key = db.execute("SELECT version, finality FROM extractions WHERE document_id=? AND config_hash=?",
                 (DOC, docs[5].run.config_hash)).fetchone()
head = db.execute("SELECT MAX(version) FROM document_versions WHERE document_id=? AND base_version=?",
                  (DOC, key["version"])).fetchone()[0]
later = db.execute("SELECT job_id, state, created_at FROM jobs WHERE input_path LIKE ? AND created_at > ? ORDER BY created_at",
                   ("%feasibility/invoice.png", verification["initial_extractions"][-1]["completed_at"])).fetchall()
print("hybrid extraction key ->", dict(key), "lineage head:", head,
      "| max version:", db.execute("SELECT MAX(version) FROM document_versions WHERE document_id=?", (DOC,)).fetchone()[0])
for job in later:
    staged = json.loads((STATE / "jobs" / job["job_id"] / "documents.json").read_text())["documents"]
    print("later job", job["job_id"][:8], job["state"], job["created_at"][:19], "staged config matches v5:",
          all(d["run"]["config_hash"] == docs[5].run.config_hash for d in staged),
          "new version rows by job:", db.execute("SELECT COUNT(*) FROM document_versions WHERE job_id=?",
                                                 (job["job_id"],)).fetchone()[0])
