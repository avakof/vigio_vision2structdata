"""Focused recheck probes for the fixes merged at 36b3200 (structural corrections M4, provenance L3).

Run from the repository root:
    .venv/bin/python -m pytest -q evidence/development/reviewer/test_recheck_probes.py

Same harness as test_review_probes.py: production ingestion, adapter, domain, Store, CLI and exports. Only
the model subprocess and readiness check are faked.
"""
from __future__ import annotations

import csv
import json
import sqlite3

from test_review_probes import _doc, _field, _pdf_with_small_image, env, invoice_reply  # noqa: F401

from vision_tables.store import Store


def _apply(env, document_id, body, name="c.json"):
    path = env.tmp / name
    path.write_text(json.dumps({"document_id": document_id, "reviewer": "r", **body}))
    return env.run("review", "apply", str(path), "--document", document_id)


def _rows(path):
    with path.open(newline="") as fp:
        return list(csv.DictReader(fp))


def test_m4_structural_add_remove_add_field_through_cli_store_replay_and_exports(env):
    # The model misses "Tasses 6,00", invents "Ghost 3,00" and omits the due date.
    env.model((invoice_reply(total="10,00", lines=(("Bols", "4,00"), ("Ghost", "3,00"))), 50, "stop"))
    out_dir = env.tmp / "out"
    code, first = env.run("extract", str(env.image), "--type", "invoice", "--output", str(out_dir))
    doc = _doc(first)
    document_id = doc["document_id"]
    assert "total_mismatch" in doc["issue_codes"]
    v1 = Store(env.state).get_document(document_id, 1)
    root = next(e for e in v1.entities if e.kind == "invoice")
    bols, ghost = [e for e in v1.entities if e.kind == "line_item"]
    body = {"base_version": 1, "approve": True, "structure": [
        {"op": "remove_row", "entity_id": ghost.id, "reason": "not printed on the page"},
        {"op": "add_row", "entity_id": "human-tasses", "kind": "line_item", "parent_id": root.id, "page": 1,
         "after_entity_id": bols.id, "reason": "printed line the model missed",
         "fields": {"description": {"raw": "Tasses", "value": "Tasses"},
                    "line_total": {"raw": "6,00", "value": "6.00"}}},
        {"op": "add_field", "entity_id": root.id, "field": "due_date", "reason": "printed in the heading",
         "reading": {"raw": "17/04/2026", "value": "2026-04-17"}}]}
    code, applied = _apply(env, document_id, body)
    assert code == 0, applied
    assert (applied["version"], applied["approval"]) == (2, "human_approved")
    assert "total_mismatch" not in applied["issue_codes"]
    # replay is idempotent; reimport serves the reviewed head; v1 keeps the model's rows
    code, replay = _apply(env, document_id, body)
    assert code == 0 and replay["replayed"] and replay["version"] == 2
    code, again = env.run("extract", str(env.image), "--type", "invoice", "--output", str(out_dir))
    assert (_doc(again)["action"], _doc(again)["version"], _doc(again)["approval"]) == ("reused", 2, "human_approved")
    store = Store(env.state)
    assert ghost.id in {e.id for e in store.get_document(document_id, 1).entities}
    v2 = store.get_document(document_id, 2)
    assert [e.id for e in v2.entities if e.kind == "line_item"] == [bols.id, "human-tasses"]
    calc = next(o for o in v2.observations if o.field == "calculated_total")
    assert calc.value == "10.00" and "human-tasses.line_total" in calc.derivation.inputs
    # JSON, relational CSV and SQLite agree on the corrected version
    code, _ = env.run("export", "--document", document_id, "--version", "2", "--format", "all", "--output", str(out_dir))
    target = out_dir / document_id / "v0002"
    canonical = json.loads((target / "document.json").read_text())
    lines = _rows(target / "tables" / "table_line_item.csv")
    assert [r["description"] for r in lines] == ["Bols", "Tasses"]
    ops = _rows(target / "tables" / "structural_corrections.csv")
    assert [(r["operation"], r["entity_id"]) for r in ops] == [
        ("remove_row", ghost.id), ("add_row", "human-tasses"), ("add_field", root.id)]
    conn = sqlite3.connect(target / "document.sqlite3")
    assert {r[0] for r in conn.execute("SELECT id FROM entities")} == {e["id"] for e in canonical["entities"]}
    assert {r[0] for r in conn.execute("SELECT id FROM observations")} == {o["id"] for o in canonical["observations"]}
    assert len(_rows(target / "tables" / "observations.csv")) == len(canonical["observations"])
    human = next(o for o in canonical["observations"] if o["id"] == "human-tasses.line_total")
    assert human["origin"] == "human_supplied" and human["evidence"][0]["channel"] == "human"


def test_m4_rejections_leave_no_version(env):
    env.model((invoice_reply(), 50, "stop"))
    code, first = env.run("extract", str(env.image), "--type", "invoice", "--output", str(env.tmp / "o"))
    document_id = _doc(first)["document_id"]
    v1 = Store(env.state).get_document(document_id, 1)
    root = next(e for e in v1.entities if e.kind == "invoice")
    line = next(e for e in v1.entities if e.kind == "line_item")
    bad = {
        "existing_id": {"op": "add_row", "entity_id": line.id, "kind": "line_item", "parent_id": root.id, "page": 1,
                        "reason": "x", "fields": {"description": {"raw": "A", "value": "A"}}},
        "orphaning_remove": {"op": "remove_row", "entity_id": root.id, "reason": "x"},
        "calculated_field": {"op": "add_field", "entity_id": root.id, "field": "calculated_total", "reason": "x",
                             "reading": {"raw": "10", "value": "10.00"}},
        "duplicate_field": {"op": "add_field", "entity_id": root.id, "field": "total", "reason": "x",
                            "reading": {"raw": "10", "value": "10.00"}},
        "noncanonical_value": {"op": "add_field", "entity_id": root.id, "field": "due_date", "reason": "x",
                               "reading": {"raw": "17/04/2026", "value": "17/04/2026"}},
        "wrong_kind": {"op": "add_row", "entity_id": "h1", "kind": "menu_item", "page": 1, "reason": "x",
                       "fields": {"name": {"raw": "A", "value": "A"}}},
        "missing_page": {"op": "add_row", "entity_id": "h2", "kind": "line_item", "parent_id": root.id, "page": 9,
                         "reason": "x", "fields": {"description": {"raw": "A", "value": "A"}}},
    }
    for name, op in bad.items():
        code, out = _apply(env, document_id, {"base_version": 1, "structure": [op]}, f"{name}.json")
        assert code == 7 and out["error"]["code"] == "invalid_correction", (name, out)
    assert [v["version"] for v in Store(env.state).versions(document_id)] == [1]
    # a removed id cannot be reused later
    code, removed = _apply(env, document_id, {"base_version": 1, "structure": [
        {"op": "remove_row", "entity_id": line.id, "reason": "x"}]}, "rm.json")
    assert code == 0 and removed["version"] == 2
    code, reuse = _apply(env, document_id, {"base_version": 2, "structure": [
        {"op": "add_row", "entity_id": line.id, "kind": "line_item", "parent_id": root.id, "page": 1, "reason": "x",
         "fields": {"description": {"raw": "A", "value": "A"}}}]}, "reuse.json")
    assert code == 7, reuse
    # structural edits on a stale base are rejected like field edits
    code, stale = _apply(env, document_id, {"base_version": 1, "structure": [
        {"op": "add_field", "entity_id": root.id, "field": "due_date", "reason": "x",
         "reading": {"raw": "17/04/2026", "value": "2026-04-17"}}]}, "stale.json")
    assert code == 6 and stale["error"]["code"] == "stale_base"


def test_l3_field_evidence_names_attempt_and_channel(env):
    env.model((invoice_reply(), 50, "stop"))
    code, out = env.run("extract", str(env.image), "--type", "invoice", "--output", str(env.tmp / "o"))
    document = Store(env.state).get_document(_doc(out)["document_id"])
    attempts = {c["attempt_id"] for c in document.run.calls}
    observed = [o for o in document.observations if o.origin == "observed"]
    assert observed and all(ref.attempt_id in attempts and ref.channel == "direct"
                            for o in observed for ref in o.evidence)


def test_m3_tiny_raster_mark_keeps_page_image_in_read(env):
    pdf_path = env.tmp / "in" / "native.pdf"
    _pdf_with_small_image(pdf_path, 0.0004)  # tiny raster below any visual threshold is still a mark now
    reply = invoice_reply(total="10,00", lines=(("Bols", "4,00"), ("Tasses", "6,00")))
    quotes = {"Atelier": "Facture F-1 Atelier", "F-1": "Facture F-1 Atelier", "EUR": "Total 10,00 EUR",
              "10,00": "Total 10,00 EUR", "Bols": "Bols 4,00", "4,00": "Bols 4,00", "Tasses": "Tasses 6,00",
              "6,00": "Tasses 6,00"}
    for record in reply["r"]:
        record["f"] = [{**f, "q": quotes.get(f["v"], f["q"])} for f in record["f"] if f["n"] != "issue_date"]
    env.model((reply, 50, "stop"))
    code, out = env.run("extract", str(pdf_path), "--type", "invoice", "--output", str(env.tmp / "o"))
    document = Store(env.state).get_document(_doc(out)["document_id"])
    channels = {ref.channel for o in document.observations if o.origin == "observed" for ref in o.evidence}
    print("NATIVE_CHANNELS", code, document.pages[0].text_layer, sorted(c or "" for c in channels),
          sorted({i.code for i in document.issues}))
    assert document.pages[0].text_layer != "usable"  # the raster mark keeps the page image in the read
