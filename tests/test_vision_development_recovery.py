import json
from pathlib import Path

import pytest

from vision_tables import runtime_local, vision
from vision_tables.config import Settings
from vision_tables.contracts import Run
from vision_tables.domain import assemble


def field(name, value, **extra):
    return dict(n=name, v=value, q=value, g=None) | extra


def row(kind, ident, parent, *fields):
    return dict(k=kind, i=ident, p=parent, f=list(fields))


def body(family, *rows):
    return json.dumps(dict(d=family, cut=False, r=list(rows)))


def read(raw, family="invoice", *, cap=False):
    return vision._payload(raw, family, {}, False, None, verified_cap=cap)


def test_duplicate_field_recovery_keeps_occurrence_and_never_chooses_contested_price():
    menu = row("menu", "root", None, field("venue_name", "Cafe"))
    repeated = row("menu_item", "r1", "root", field("name", "Soup"), field("name", "Soup"),
                   field("price", "4,00"), field("price", "7,00"), field("currency", "EUR", g="forged"))
    invalid = row("line_item", "r2", "root", field("description", "Soup"))
    clean = row("menu_item", "r3", "root", field("name", "Soup"), field("price", "8,00"),
                field("description", "Fresh", bbox=[1, 2, 3, 4]))
    payload, _, findings, _ = read(body("menu", menu, repeated, invalid, clean), "menu")
    assert [r.local_id for r in payload.records] == ["root", "r1", "r3"]
    assert findings == {"model_field_duplicate": 2, "model_field_omitted": 2, "model_record_omitted": 1}
    a, b = payload.records[1:]
    assert a.fields["name"].raw == b.fields["name"].raw == "Soup"
    assert a.fields["price"].raw is None and a.fields["price"].state == "ambiguous"
    assert a.fields["price"].alternatives == ["4,00", "7,00"]
    assert b.fields["price"].raw == "8,00"
    assert "currency" not in a.fields and "description" not in b.fields


def test_duplicate_ids_recover_occurrences_only_against_unique_compatible_parent_kind():
    payload, _, findings, unresolved = read(body("invoice",
        row("invoice", "x", None, field("total", "8,00")),
        row("line_item", "x", "x", field("description", "Soup"), field("line_total", "4,00")),
        row("line_item", "y", "x", field("description", "Soup"), field("line_total", "4,00")),
        row("charge", "fee", "x", field("amount", "1,00"))))
    root, first, second, charge = payload.records
    assert len({record.local_id for record in payload.records}) == 4
    assert first.parent_id == second.parent_id == root.local_id
    assert charge.parent_id is None and unresolved == {charge.local_id}
    assert findings == {"model_record_id_recovered": 2, "model_parent_unresolved": 1}


@pytest.mark.parametrize("ending", ['{"k": "line_it', '{"k": "line_item", "i": "r2", "f": [{"q": "hello\\u12', ''])
def test_verified_length_cap_only_keeps_closed_records(ending):
    original = body("invoice", row("invoice", "root", None, field("total", "8,00")),
                    row("line_item", "r1", "root", field("description", "Soup")))
    truncated = original[:-2] + ", " + ending
    with pytest.raises(ValueError):
        read(truncated)
    payload, _, findings, _ = read(truncated, cap=True)
    assert [r.local_id for r in payload.records] == ["root", "r1"]
    assert findings == {"model_prefix_recovered": 1}


@pytest.mark.parametrize("ending", ['{"k": "line_item", "k":', 'nullX', ']', '{"k", "i": "r2', '{"q": "abc\\x'])
def test_length_cap_does_not_license_malformed_or_duplicate_key_json(ending):
    original = body("invoice", row("invoice", "root", None, field("total", "8,00")))
    with pytest.raises(ValueError):
        read(original[:-2] + ", " + ending, cap=True)


def test_assembly_respects_explicit_unresolved_parent_without_changing_absent_parent_behavior(tmp_path, monkeypatch):
    src = Path(__file__).resolve().parents[1] / "fixtures/feasibility/invoice.png"
    settings = Settings(doc_type="invoice", retries=0, max_crops=0)
    ingestion = vision.inspect_source(src, tmp_path, settings)
    raw = body("invoice", row("invoice", "root", None, field("total", "8,00")),
               row("line_item", "r1", "missing", field("description", "Soup")),
               row("line_item", "r2", None, field("description", "Soup")))
    monkeypatch.setattr(runtime_local, "call", lambda *args: dict(raw=raw, generated_tokens=120, finish_reason="stop"))
    reading = vision.read_page(ingestion.pages[0], [], tmp_path, settings)
    assert reading.incomplete and reading.calls[0]["effective_unresolved_parent_ids"] == ["r1"]
    run = Run(job_id="test", pipeline="direct", model="fixture", revision="test", runtime={}, settings={},
              config_hash="0" * 64, calls=reading.calls)
    document = assemble(ingestion, [reading], [1], run, "doc-test")
    assert [entity.parent_id for entity in document.entities if entity.kind == "line_item"] == [None, "p1.root"]


def test_reread_keeps_prices_within_their_identified_menu_section():
    menu = row("menu", "root", None, field("venue_name", "Cafe"))
    starters = row("section", "r1", "root", field("name", "Starters"))
    mains = row("section", "r2", "root", field("name", "Mains"))
    starter = row("menu_item", "r3", "r1", field("name", "Soup"), field("price", "4,00"))
    main = row("menu_item", "r4", "r2", field("name", "Soup"), field("price", "8,00"))
    first, *_ = read(body("menu", menu, starters, starter, mains, main), "menu")
    second, *_ = read(body("menu", menu, mains, main, starters, starter), "menu")
    assert vision._merge(first, second, crop=False) == (False, False)
    assert [r.fields["price"].raw for r in first.records if r.kind == "menu_item"] == ["4,00", "8,00"]
