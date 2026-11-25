# Reasoner delta verification

[Independent findings and gate disposition](../../docs/development/final-delta-audit.md)

| Evidence | Value |
| --- | --- |
| Integrated tree reviewed | `0a5a3fdff7100cbc08b483945b31f4f7c519c1cf`. Opus independently rechecked the earlier `36b3200aa93d8faa3d4e6eeda3b9edb66ec6d141`. |
| Local review tree | `git merge --no-edit main` produced `b2080f6f2e9fa35793b00ff03db08041df870e70` on `vt-reasoner`; `git diff HEAD 0a5a3fd` was empty before adding these reports. |
| Independent turn | Orca task `task_4a14b2a33b60`, dispatch `ctx_c0b5f3d31ce3`, worker session `6bb80f99-cd2d-46b1-842d-dbd4079bf178`. Codex thread `01a21339-ac34-7863-8028-c56b2f332efe`, turn `01a213ad-1624-7421-b5d2-cac722967663` recorded `gpt-6.2-astra`, `xhigh` at `2026-11-25T17:07:23.237Z`. |
| Scope | Read-only production audit. The model boundary used controlled replies for the blank-block probe. Timings and native token counts came from existing unmodified local artifacts. No downloads, held-out predictions, or model runs were performed. |

## Existing tests

Ran from the merged worktree with its `src` first on the import path and the locked core interpreter. The first invocation passed 114 tests in 3.92 s, the second 67 in 1.28 s.

```sh
export PYTHONPATH=src PYTHONDONTWRITEBYTECODE=1
vt_python=/Users/vakof/orca/vision-to-tables/.venv/bin/python
"$vt_python" -m pytest -q -p no:cacheprovider tests/test_qa_evidence_metadata.py tests/test_vision_parser_tokens.py tests/test_store.py tests/test_store_exports.py tests/test_vision.py evidence/development/reviewer
"$vt_python" -m pytest -q -p no:cacheprovider tests/test_domain_assemble.py tests/test_qa_evaluate.py
```

The exact historical migration 001 and 002 SQL statement tuples matched `git show 36b3200:src/vision_tables/migrations.py`, including both checksums. On an in-memory 002 database, a failing statement appended immediately after the first 003 backfill rolled the transaction back. The ordered `sqlite_schema` rows, including the two update triggers, equaled the pre-upgrade snapshot, and retry applied only migration 003.

The fixture check used `evaluate.load_manifest` on each archival manifest, checked a disjoint union of IDs, families and input hashes, opened all seven images, and compared both decoded manuscript crops with their exact parent-page rectangles. The two local Paddle pass directories under `.vt-state/paddle/output` yielded JSON SHA-256 `29f0256727d6775dac1e55ea85a52bce094c2947f1319a9161367e6606092f5e` and Markdown SHA-256 `62d6a922b0ece6d88e57d1b567db8e7f2d907a2e6ad3328ef4e490ee5e447631` in each pass. The committed 32-token receipt and local `production-budget32/observed.json` both hashed to `11d98b34c8d7a22a0eb1386d002caf71a9acbc10334119b973d401f0d693b519`. The separate original `reply.json` supplied the same telemetry and rectangles. The live dependency check used `installed_versions` against `paddle/env/bin/python` and `runtime_pins("paddle-requirements.txt")`.

## Additional reproduced cases

These use production assembly, review, storage, exports and adapter logic with tiny synthetic inputs. Run the Python block in the locked core environment from the reviewed checkout.

```python
import csv
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from vision_tables import exports, runtime_local, vision
from vision_tables.config import Settings
from vision_tables.contracts import Box, CorrectionSet, PageReading, Region
from vision_tables.domain import assemble, review_gate
from vision_tables.domain.examples import EXAMPLES, example_context, f, rec
from vision_tables.store import Store


def reclassify(root):
    store = Store(root / "state")
    ing, run = example_context()
    for index, family in enumerate(("invoice", "menu"), 1):
        job = store.create_job(kind="extract", input_path="synthetic.png",
                               output_dir=str(root / "out"), settings_json="{}")
        version_run = run.model_copy(update={"job_id": job.job_id, "config_hash": str(index) * 64})
        candidate = assemble(ing, [EXAMPLES[family]()], [1], version_run, "same-doc")
        latest = store.persist_candidates(job.job_id, job.token, [candidate])[0].document

    def publish(version):
        result = exports.publish(store, "same-doc", version, root / "out", ("sqlite", "csv", "json"))
        return Path(result["directory"])

    path = publish(latest.version)
    with (path / "tables/documents.csv").open() as stream, sqlite3.connect(path / "document.sqlite3") as db:
        relational = db.execute("SELECT doc_type FROM documents").fetchone()[0]
        embedded = db.execute("SELECT json_extract(snapshot_json,'$.doc_type') FROM document_versions").fetchone()[0]
        canonical = json.loads((path / "document.json").read_bytes())["doc_type"]
        print("list, standalone row, CSV, JSON, embedded", store.list_documents()[0]["doc_type"],
              relational, next(csv.DictReader(stream))["doc_type"], canonical, embedded)
        assert not db.execute("PRAGMA foreign_key_check").fetchall()

    event = CorrectionSet(document_id="same-doc", base_version=1, reviewer="R", changes=[
        {"observation_id": "p1.inv.supplier_name", "value": "Maison Dupond", "reason": "Checked name."}
    ])
    third = store.apply_corrections("same-doc", event).document
    with sqlite3.connect(publish(third.version) / "document.sqlite3") as db:
        links = db.execute("SELECT version,parent_version,snapshot_sha256 FROM export_lineage ORDER BY version")
        assert [(v, parent, sha == store.snapshot("same-doc", v)[1]) for v, parent, sha in links] == [
            (1, None, True), (3, 1, True)]
        assert db.execute("SELECT version FROM document_versions").fetchall() == [(3,)]
        assert db.execute("SELECT base_version,result_version FROM corrections").fetchall() == [(1, 3)]
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def menu_pair():
    ing, run = example_context()
    for kind, bounds, precision in (("page_only", None, None), ("reread_tile", (0, 0, 1000, 1400), "crop"),
                                    ("native_text", (10, 20, 85, 40), "native_text")):
        regions = [Region(id="r", page=1, order=0, kind=kind, text="Soupe" if kind == "native_text" else "",
                          box=Box(xyxy=bounds, precision=precision))] if bounds else []
        reading = PageReading(page=1, doc_type="menu", regions=regions, records=[
            rec("menu", "m", venue_name=f("Cafe"), currency=f("EUR")),
            rec("menu_item", "i", "m", name=f("Soupe", language="fr", region_id="r" if bounds else None),
                translated_name=f("Roast chicken", language="en"), price=f("4,50")),
        ])
        doc = assemble(ing, [reading], [1], run, "doc")
        print(kind, doc.validation, review_gate(doc)["approvable"], [(i.code, i.severity) for i in doc.issues])


def empty_text_block(root):
    settings = Settings(doc_type="invoice", pipeline="hybrid", max_tokens=32, retries=0, max_crops=0)
    ing = vision.inspect_source(Path("fixtures/feasibility/invoice.png"), root, settings)
    page, = ing.pages
    blocks = [{"block_bbox": b, "block_label": "text", "block_content": content}
              for b, content in (([20, 30, 180, 60], "TOTAL 37,25 EUR"), ([20, 100, 180, 140], ""))]
    crops = [dict(crop_index=i, generated_tokens=n, token_limit=32, cap_hit=False, eos_seen=True)
             for i, n in ((1, 9), (2, 1))]

    def reply(kind, request, *unused):
        if kind == "paddle":
            return dict(width=page.image_width, height=page.image_height, blocks=blocks, omitted_regions=0,
                        layout={"boxes": [{"coordinate": b["block_bbox"]} for b in blocks]},
                        recognition_tokens=dict(requested_crops=2, returned_crops=2, crops=crops))
        field = dict(n="total", v="37,25", q="TOTAL 37,25 EUR", g="p1-parser-0")
        payload = dict(d="invoice", cut=False, r=[dict(k="invoice", i="a", p=None, f=[field])])
        return dict(generated_tokens=20, finish_reason="stop", raw=json.dumps(payload))

    with patch.object(vision, "hybrid_info", return_value={"ready": True, "model": "fixture"}), \
         patch.object(runtime_local, "call", reply):
        reading = vision.read_page(page, ing.regions, root, settings)
    _, run = example_context(pipeline="hybrid")
    doc = assemble(ing, [reading], [1], run.model_copy(update={"calls": reading.calls}), "doc")
    print("parser", reading.calls[0]["status"], reading.calls[0]["degraded_blocks"],
          [r.id for r in reading.regions], [i.code for i in reading.issues], doc.coverage.incomplete_pages)


with TemporaryDirectory() as tmp:
    reclassify(Path(tmp))
    menu_pair()
    empty_text_block(Path(tmp))
```

Observed output on the reviewed SHA:

```text
list, standalone row, CSV, JSON, embedded invoice invoice menu menu menu
page_only valid True [('translation_without_layout_evidence', 'info')]
reread_tile valid True []
native_text valid True []
parser parsed 0 ['p1-parser-0'] [] []
```
