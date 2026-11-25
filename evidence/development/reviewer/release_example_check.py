"""Offline check of the committed correction example (no inference, no store mutation of real state).

Run: .venv/bin/python -I evidence/development/reviewer/release_example_check.py   (from the repository root)
Re-applies evidence/examples/invoice/corrections.json to the exported v2 snapshot with the current domain code,
compares with the exported v3, and checks manifest digests and JSON/CSV/SQLite agreement for each version.
"""
import csv
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))
from vision_tables.contracts import CorrectionSet, Document  # noqa: E402
from vision_tables.domain import apply_corrections  # noqa: E402

base = Path("evidence/examples/invoice")
root = base / "doc-5f704466a44dd696b8317362adec39ec"
docs = {}
for vdir in sorted(root.iterdir()):
    manifest = json.loads((vdir / "manifest.json").read_text())
    files_ok = all(hashlib.sha256((vdir / name).read_bytes()).hexdigest() == digest
                   for name, digest in manifest["files"].items())
    raw = (vdir / "document.json").read_bytes()
    doc = Document.model_validate_json(raw)
    docs[doc.version] = doc
    with (vdir / "tables" / "observations.csv").open(newline="") as fp:
        table = {r["observation_id"]: r for r in csv.DictReader(fp)}
    conn = sqlite3.connect(f"file:{vdir / 'document.sqlite3'}?mode=ro", uri=True)
    sql = {r[0]: r for r in conn.execute("SELECT id, value, state, origin FROM observations")}
    sql_entities = {r[0] for r in conn.execute("SELECT id FROM entities")}
    agree = (set(table) == set(sql) == {o.id for o in doc.observations}
             and sql_entities == {e.id for e in doc.entities}
             and all(sql[o.id][2] == o.state and table[o.id]["state"] == o.state for o in doc.observations))
    print(f"{vdir.name}: manifest_files_ok={files_ok} snapshot_sha_ok="
          f"{hashlib.sha256(raw).hexdigest() == manifest['snapshot_sha256']} json_csv_sqlite_agree={agree} "
          f"version={doc.version} parent={doc.parent_version} run={doc.run.pipeline} max_tokens={doc.run.settings.get('max_tokens')} "
          f"entities={[e.id for e in doc.entities]} approval={doc.approval} validation={doc.validation}")
    print("   values:", {o.id: o.value for o in doc.observations if o.state == "readable"})

event = CorrectionSet.model_validate(json.loads((base / "corrections.json").read_text()))
replayed = apply_corrections(docs[2], event)
strip = lambda d: {k: v for k, v in d.model_dump(mode="json").items()}  # noqa: E731
same = strip(replayed) == strip(docs[3])
print("reapplied corrections to v2 equals exported v3:", same)
if not same:
    a, b = strip(replayed), strip(docs[3])
    print("   differing keys:", [k for k in a if a[k] != b.get(k)])
print("v2 retains removed row:", "p1.p1-parser-1" in {e.id for e in docs[2].entities},
      "| v3 lacks it:", "p1.p1-parser-1" not in {e.id for e in docs[3].entities})
