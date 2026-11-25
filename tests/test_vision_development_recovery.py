import json
from pathlib import Path

import pytest

from vision_tables import runtime_local, vision
from vision_tables.config import Settings
from vision_tables.contracts import PageReading, Region, Run
from vision_tables.domain import assemble, review_gate


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
    assert vision._merge(first, second, crop=False) == (False, False, 0)
    assert [r.fields["price"].raw for r in first.records if r.kind == "menu_item"] == ["4,00", "8,00"]


def page_result(tmp_path, monkeypatch, family, *bodies, regions=()):
    src = Path(__file__).resolve().parents[1] / "fixtures/feasibility/invoice.png"
    settings = Settings(doc_type=family, retries=len(bodies) - 1, max_crops=0)
    ingestion = vision.inspect_source(src, tmp_path, settings)
    page, = ingestion.pages
    if regions:
        page.text_layer = "usable"
        ingestion.regions.extend(regions)
    replies = iter(dict(raw=raw, generated_tokens=120, finish_reason="stop") for raw in bodies)
    monkeypatch.setattr(runtime_local, "call", lambda *args: next(replies))
    reading = vision.read_page(page, regions, tmp_path, settings)
    run = Run(job_id="test", pipeline="direct", model="fixture", revision="test", runtime={}, settings={},
              config_hash="0" * 64, calls=reading.calls)
    return reading, assemble(ingestion, [reading], [1], run, "doc-test")


def test_richer_reread_keeps_explicit_parent_uncertainty_with_different_ids(tmp_path, monkeypatch):
    root = row("invoice", "root", None, field("total", "8,00"))
    before = body("invoice", root, row("line_item", "r1", "missing", field("description", "Paper")))
    after = body("invoice", root, row("line_item", "r2", None, field("description", "Paper")),
                 row("line_item", "r1", None, field("description", "Ink")))
    reading, doc = page_result(tmp_path, monkeypatch, "invoice", before, after)
    assert reading.incomplete
    assert reading.calls[0]["unresolved_parent_ids"] == ["r1"]
    assert "unresolved_parent_ids" not in reading.calls[1]
    effective = reading.calls[1]["effective_unresolved_parent_ids"]
    assert effective == ["r2"]
    assert json.loads((tmp_path / "attempts/p1-qwen-1/attempt.json").read_text())["effective_unresolved_parent_ids"] == effective
    assert {entity.id: entity.parent_id for entity in doc.entities if entity.kind == "line_item"} == {
        "p1.r2": None, "p1.r1": "p1.root"}


def test_richer_reread_cannot_replace_an_unresolved_row_with_indistinguishable_occurrences(tmp_path, monkeypatch):
    root = row("invoice", "root", None, field("total", "8,00"))
    before = body("invoice", root, row("line_item", "r1", "missing", field("description", "Paper")))
    after = body("invoice", root, *(row("line_item", ident, None, field("description", "Paper"))
                                   for ident in ("r2", "r3")))
    reading, doc = page_result(tmp_path, monkeypatch, "invoice", before, after)
    assert reading.incomplete and "reread_structure_disagreement" in {issue.code for issue in reading.issues}
    assert reading.calls[0]["effective_unresolved_parent_ids"] == ["r1"]
    assert {entity.id: entity.parent_id for entity in doc.entities if entity.kind == "line_item"} == {"p1.r1": None}


@pytest.mark.parametrize("changed", [False, True])
def test_whole_page_rereads_compare_occurrences_in_both_directions(tmp_path, monkeypatch, changed):
    def menu(currency, second_name):
        return body("menu", row("menu", "root", None, field("venue_name", "Cafe"),
                               field("currency", currency, q=currency or "Unclear")),
                    row("menu_item", "r1", "root", field("name", "Soup"), field("price", "4")),
                    row("menu_item", "r2", "root", field("name", second_name), field("price", "4")))

    first_names = ["Soup", "Salad" if changed else "Soup"]
    reading, doc = page_result(tmp_path, monkeypatch, "menu", menu(None, first_names[1]), menu("EUR", "Soup"))
    assert reading.incomplete == changed
    assert ("reread_structure_disagreement" in {issue.code for issue in reading.issues}) == changed
    assert doc.coverage.incomplete_pages == ([1] if changed else [])
    assert [r.fields["name"].raw for r in reading.records if r.kind == "menu_item"] == first_names


@pytest.mark.parametrize("followup, disagreed", [(("7", "4"), True), (("6", "4"), False)])
def test_duplicate_rows_keep_prices_and_flag_different_multisets(tmp_path, monkeypatch, followup, disagreed):
    def menu(currency, prices):
        root = row("menu", "root", None, field("venue_name", "Cafe"), field("currency", currency, q=currency or "Unclear"))
        return body("menu", root, *(row("menu_item", f"r{index}", "root", field("name", "Soup"), field("price", price))
                                    for index, price in enumerate(prices, 1)))

    reading, doc = page_result(tmp_path, monkeypatch, "menu", menu(None, ("4", "6")), menu("EUR", followup))
    assert reading.incomplete == disagreed
    assert ("reread_structure_disagreement" in {issue.code for issue in reading.issues}) == disagreed
    assert doc.coverage.incomplete_pages == ([1] if disagreed else [])
    assert review_gate(doc)["approvable"] != disagreed
    assert [r.fields["price"].raw for r in reading.records if r.kind == "menu_item"] == ["4", "6"]
    assert [e.attempt_id for r in ("r1", "r2") for e in reading.field_evidence[r]["price"]] == ["p1-qwen-0"] * 2


@pytest.mark.parametrize("richer", [False, True])
@pytest.mark.parametrize("contested_first", [False, True])
def test_contested_price_survives_reread_selection(tmp_path, monkeypatch, contested_first, richer):
    menu = row("menu", "root", None, field("venue_name", "Cafe"), field("currency", None, q="Unclear"))
    # Force recovery-aware selection in both arrival orders with otherwise harmless repeated data.
    first_menu = row("menu", "root", None, *menu["f"], field("venue_name", "Cafe"))
    contested = row("menu_item", "r1", "root", field("name", "Soup"), field("price", "4"), field("price", "7"))
    single = row("menu_item", "r2", "root", field("name", "Soup"), field("price", "9"))
    first, second = (contested, single) if contested_first else (single, contested)
    extra = [row("menu_item", "r3", "root", field("name", "Bread"))] if richer else []
    reading, doc = page_result(tmp_path, monkeypatch, "menu", body("menu", first_menu, first),
                               body("menu", menu, second, *extra))
    price, = [obs for obs in doc.observations if obs.field == "price"]
    assert price.value is None and price.state == "ambiguous"
    expected = ["4", "7", "9"] if contested_first else ["9", "4", "7"]
    assert price.alternatives == expected
    assert [e.quote for e in price.evidence] == expected
    assert {e.attempt_id for e in price.evidence} == {"p1-qwen-0", "p1-qwen-1"}
    assert "reread_disagreement" in {issue.code for issue in reading.issues}
    assert any(r.local_id == "r3" for r in reading.records) == richer


def test_contested_total_retains_both_real_native_citations(tmp_path, monkeypatch):
    spans = [Region(id=f"span-{index}", page=1, order=index, kind="native_text", text=f"Total {value}")
             for index, value in enumerate(("4.00", "7.00"))]
    invoice = body("invoice", row("invoice", "root", None,
                   *(field("total", span.text.split()[-1], g=span.id, q=span.text) for span in spans)))
    reading, doc = page_result(tmp_path, monkeypatch, "invoice", invoice, regions=spans)
    total, = [obs for obs in doc.observations if obs.field == "total"]
    assert total.value is None
    assert [(e.region_id, e.quote, e.channel, e.attempt_id) for e in total.evidence] == [
        (span.id, span.text, "native_text", "p1-qwen-0") for span in spans]
    assert PageReading.model_validate_json(reading.model_dump_json()).field_evidence["root"]["total"] == total.evidence


@pytest.mark.parametrize("incoming", ["17", "16"])
def test_reread_alternative_bound_retains_proposals_and_counts_omissions(tmp_path, monkeypatch, incoming):
    def menu(prices):
        return body("menu", row("menu", "root", None, field("venue_name", "Cafe")),
                    row("menu_item", "r1", "root", field("name", "Soup"), *(field("price", price) for price in prices)))

    retained = list(map(str, range(1, 17)))
    reading, doc = page_result(tmp_path, monkeypatch, "menu", menu(retained), menu([incoming]))
    parsed = PageReading.model_validate_json(reading.model_dump_json())
    price = parsed.records[1].fields["price"]
    assert price.raw is None and price.state == "ambiguous" and price.alternatives == retained
    assert parsed.incomplete and doc.coverage.incomplete_pages == [1]
    assert next(f["raw"] for f in parsed.calls[1]["fields"] if f["field"] == "price") == incoming
    issues = [issue for issue in parsed.issues if issue.code == "model_alternative_limit"]
    if incoming == "17":
        assert len(issues) == 1 and "Omitted 1 distinct" in issues[0].message
        assert parsed.calls[1]["reread_alternatives_omitted"] == 1
        assert all(e.quote != incoming for e in parsed.field_evidence["r1"]["price"])
    else:
        assert not issues and "reread_alternatives_omitted" not in parsed.calls[1]
        assert not any(issue.code == "reread_disagreement" for issue in parsed.issues)
        assert parsed.field_evidence["r1"]["price"][-1].attempt_id == "p1-qwen-1"


def test_same_reply_alternative_limit_counts_distinct_omissions(tmp_path, monkeypatch):
    root = row("invoice", "root", None, *(field("total", str(n)) for n in [*range(1, 19), 18]))
    reading, _ = page_result(tmp_path, monkeypatch, "invoice", body("invoice", root))
    assert reading.incomplete
    assert reading.calls[0]["recovery_issues"]["model_alternative_limit"] == 2
    assert any(i.code == "model_alternative_limit" and "Omitted 2 distinct" in i.message for i in reading.issues)
    assert len(reading.field_evidence["root"]["total"]) == 16
