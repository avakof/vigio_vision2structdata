import pytest
from jsonschema import Draft202012Validator

from vision_tables.config import ProcessingError
from vision_tables.contracts import Document, FieldRead, Ingested, PageReading, Region
from vision_tables.domain import assemble, review_gate, revalidate
from vision_tables.domain.examples import EXAMPLES, example_context, f, rec

SCHEMA = Draft202012Validator(Document.model_json_schema())


def build(readings, pages=1, scope=None, ingested=None, run=None, **ing):
    base, base_run = example_context(pages)
    if ing:
        base = base.model_copy(update=ing)
    document = assemble(ingested or base, readings if isinstance(readings, list) else [readings],
                        scope or list(range(1, pages + 1)), run or base_run, "doc-test")
    SCHEMA.validate(document.model_dump(mode="json"))
    assert revalidate(document) == document          # assembly and revalidation share one path
    return document


def codes(doc, severity=None):
    return [i.code for i in doc.issues if severity in (None, i.severity)]


def obs(doc, entity_suffix, field):
    return next(o for o in doc.observations if o.entity_id.endswith(entity_suffix) and o.field == field)


@pytest.mark.parametrize("kind", ["invoice", "menu", "recipe"])
def test_examples_assemble_validate_and_revalidate_identically(kind):
    document = build(EXAMPLES[kind]())
    assert document.doc_type == kind and document.approval == "needs_review"
    SCHEMA.validate(document.model_dump(mode="json"))
    assert revalidate(document) == document
    assert Document.model_validate_json(document.model_dump_json()) == document


def test_invoice_example_derives_values_without_touching_observed_ones():
    document = build(EXAMPLES["invoice"]())
    assert document.validation == "valid" and not [i for i in document.issues if i.severity == "review"]
    date = obs(document, "inv", "issue_date")
    assert (date.raw, date.value) == ("03/04/2026", "2026-04-03") and "dmy" in date.reason
    pack = obs(document, "l1", "pack_total_quantity")
    assert (pack.value, pack.origin) == ("12", "calculated")
    assert pack.derivation.inputs == ["p1.l1.quantity", "p1.l1.pack_count", "p1.l1.pack_unit_quantity", "p1.l1.pack_unit"]
    assert obs(document, "l1", "calculated_line_total").value == "7.20"
    assert obs(document, "l1", "line_total").value == "7.20" and obs(document, "l1", "line_total").origin == "observed"
    assert obs(document, "inv", "calculated_total").value == obs(document, "inv", "total").value == "27.11"
    assert obs(document, "inv", "total").raw == "27,11 €"
    assert obs(document, "inv", "currency").value == "EUR"


def test_source_order_and_distinct_ordinals_are_preserved():
    document = build(EXAMPLES["invoice"]())
    assert [e.id for e in document.entities] == ["p1.inv", "p1.l1", "p1.l2", "p1.t1"]
    assert [e.ordinal for e in document.entities] == [0, 1, 2, 3]
    assert [e.parent_id for e in document.entities] == [None, "p1.inv", "p1.inv", "p1.inv"]
    observed = [o.field for o in document.observations if o.entity_id == "p1.l1" and o.origin == "observed"]
    assert observed == ["description", "quantity", "purchase_unit", "pack_count", "pack_unit_quantity", "pack_unit",
                        "unit_price", "price_basis", "line_total"]


def test_repeated_identical_rows_survive_across_pages_and_within_a_page():
    row = lambda i: rec("line_item", i, None, description=f("Eau 1 L"), quantity=f("1"), unit_price=f("2,00"), line_total=f("2,00"))
    page1 = PageReading(page=1, doc_type="invoice", records=[rec("invoice", "i", total=f("8,00")), row("a"), row("a")])
    page2 = PageReading(page=2, doc_type="invoice", records=[row("a")])
    document = build([page1, page2], pages=2)
    lines = [e for e in document.entities if e.kind == "line_item"]
    assert len(lines) == 3 and len({e.id for e in lines}) == 3
    assert [e.page for e in lines] == [1, 1, 2]
    assert "duplicate_local_id" in codes(document) and "duplicate_row" in codes(document)
    assert all(i.severity == "info" for i in document.issues if i.code in ("duplicate_row", "duplicate_local_id"))
    assert {e.parent_id for e in lines} == {"p1.i"}  # page 2 continuation joined to the single root, with a note
    assert any(i.code == "parent_joined" and i.page == 2 for i in document.issues)


def test_unsupported_output_is_rejected_loudly_not_silently():
    bogus = rec("invoice", "i", total=f("5,00"))
    bogus.fields["table_number"] = f("12")                          # outside the vocabulary
    bogus.fields["calculated_total"] = f("5,00")                    # domain-only field
    foreign = rec("menu_item", "m", name=f("Soupe"))   # wrong family for an invoice reading
    reading = PageReading.model_construct(page=1, doc_type="invoice", records=[bogus, foreign], regions=[], issues=[],
                                          incomplete=False, suspected_cutoff=False, calls=[])
    document = build(reading)
    assert codes(document).count("unsupported_field") == 2 and "unsupported_kind" in codes(document)
    assert {o.field for o in document.observations} == {"total"}
    assert all(i.severity == "review" for i in document.issues if i.code.startswith("unsupported"))
    assert not review_gate(document)["approvable"]


def test_invalid_evidence_is_distinct_from_an_unresolved_reading():
    region = Region(id="r1", page=1, order=0, kind="text", text="Total TTC 27,11 €")
    ingested, run = example_context()
    ingested = ingested.model_copy(update={"regions": [region]})
    reading = PageReading(page=1, doc_type="invoice", records=[rec("invoice", "i",
                          total=FieldRead(raw="27,11", evidence="Total TTC 27,11", region_id="r1"),
                          subtotal=FieldRead(raw="25,70", evidence="Total HT 25,70", region_id="missing"),
                          currency=FieldRead(raw="£?", state="ambiguous", alternatives=["GBP", "EGP"]))])
    document = assemble(ingested, [reading], [1], run, "doc-test")
    assert obs(document, "i", "total").evidence[0].region_id == "r1"
    assert obs(document, "i", "subtotal").evidence[0].region_id is None
    invalid = next(i for i in document.issues if i.code == "invalid_evidence")
    assert invalid.observation_ids == ["p1.i.subtotal"]
    currency = obs(document, "i", "currency")
    assert (currency.state, currency.value, currency.alternatives) == ("ambiguous", None, ["GBP", "EGP"])
    assert any(i.code == "ambiguous_reading" and i.observation_ids == [currency.id] for i in document.issues)


def test_evidence_must_support_the_reading():
    region = Region(id="r1", page=1, order=0, kind="text", text="Net à payer 99,00")
    ingested, run = example_context()
    ingested = ingested.model_copy(update={"regions": [region]})
    reading = PageReading(page=1, doc_type="invoice", records=[rec("invoice", "i",
                          total=FieldRead(raw="27,11", evidence="Total 27,11", region_id="r1"),
                          document_number=FieldRead(raw="FA-1"))])
    document = assemble(ingested, [reading], [1], run, "doc-test")
    assert {"evidence_not_in_region"} <= set(codes(document, "review"))   # critical field: blocks approval
    assert next(i for i in document.issues if i.code == "ungrounded_reading").severity == "info"  # non-critical


def test_unresolved_reading_states_are_kept_null_with_reasons():
    reading = PageReading(page=1, doc_type="invoice", records=[rec("invoice", "i",
        issue_date=f("03/04/2026"), due_date=FieldRead(raw=None, state="unreadable", reason="smudged"),
        customer_name=FieldRead(raw=None, state="not_present"),
        total=FieldRead(raw="8.50", evidence="8.50", state="ambiguous", alternatives=["8.50", "6.50"]))])
    document = build(reading)
    date = obs(document, "i", "issue_date")
    assert (date.value, date.state, date.alternatives) == (None, "ambiguous", ["2026-04-03", "2026-03-04"])
    assert date.raw == "03/04/2026"
    assert obs(document, "i", "due_date").state == "unreadable" and obs(document, "i", "due_date").value is None
    assert obs(document, "i", "customer_name").state == "not_present"
    assert obs(document, "i", "total").alternatives == ["8.50", "6.50"]
    assert codes(document).count("ambiguous_reading") == 2 and codes(document).count("unreadable_field") == 1
    assert not any(o.origin == "calculated" for o in document.observations)


def test_decimal_formats_normalize_equally_but_stay_raw():
    reading = PageReading(page=1, doc_type="invoice", records=[
        rec("invoice", "i", subtotal=f("1 234,50"), total=f("1,234.50"))])
    document = build(reading)
    assert obs(document, "i", "subtotal").value == obs(document, "i", "total").value == "1234.50"
    assert (obs(document, "i", "subtotal").raw, obs(document, "i", "total").raw) == ("1 234,50", "1,234.50")


def test_totals_separate_printed_from_calculated_and_report_mismatch_without_editing():
    reading = PageReading(page=1, doc_type="invoice", records=[
        rec("invoice", "i", subtotal=f("10,00"), tax_total=f("2,00"), total=f("13,00")),
        rec("line_item", "a", "i", description=f("A"), quantity=f("2"), unit_price=f("5,00"), line_total=f("10,00"))])
    document = build(reading)
    assert obs(document, "i", "total").value == "13.00" and obs(document, "i", "calculated_total").value == "12.00"
    mismatch = next(i for i in document.issues if i.code == "total_mismatch")
    assert mismatch.severity == "review" and mismatch.observation_ids == ["p1.i.total", "p1.i.calculated_total"]
    assert not review_gate(document)["approvable"]


def test_line_discount_pack_basis_and_tax_variants():
    reading = PageReading(page=1, doc_type="invoice", records=[
        rec("invoice", "i", currency=f("EUR")),
        rec("line_item", "a", "i", description=f("Lait"), quantity=f("2"), purchase_unit=f("cartons"), pack_count=f("6"),
            pack_unit_quantity=f("1"), pack_unit=f("L"), unit_price=f("1,00"), price_basis=f("/L"), discount_rate=f("10 %"),
            line_total=f("10,80")),
        rec("line_item", "b", "i", description=f("Beurre"), quantity=f("3"), unit_price=f("2,00"), tax_rate=f("20 %"), line_total=f("7,20"))])
    document = build(reading)
    assert obs(document, "a", "calculated_line_total").value == "10.80"
    assert "line_total_mismatch" not in codes(document)
    assert "line_total_matches_with_tax" in codes(document, "info")      # 6.00 * 1.2 = 7.20, tax inclusion is not inferred
    assert obs(document, "b", "calculated_line_total").value == "6.00"


def test_rounding_policy_is_explicit_per_line_and_visible_when_the_total_rounds_once():
    lines = [rec("line_item", c, "i", description=f("x"), quantity=f("1"), unit_price=f("0,335"), line_total=f("0,34")) for c in "abc"]
    reading = PageReading(page=1, doc_type="invoice", records=[rec("invoice", "i", currency=f("EUR"), total=f("1,01")), *lines])
    document = build(reading)
    assert [obs(document, c, "calculated_line_total").value for c in "abc"] == ["0.34"] * 3  # half away from zero per line
    assert obs(document, "i", "calculated_total").value == "1.02"
    assert "rounding_policy_difference" in codes(document, "info") and "total_mismatch" not in codes(document)
    assert "round_half_up" in obs(document, "i", "calculated_total").derivation.calculation
    other = PageReading(page=1, doc_type="invoice", records=[rec("invoice", "i", currency=f("EUR"), total=f("1,50")), *lines])
    assert "total_mismatch" in codes(build(other), "review")


def test_credit_note_signs_are_preserved_as_printed():
    reading = PageReading(page=1, doc_type="invoice", records=[
        rec("invoice", "i", document_kind=f("Avoir"), total=f("-12,00")),
        rec("line_item", "a", "i", description=f("Retour"), quantity=f("-1"), unit_price=f("12,00"), line_total=f("(12,00)"))])
    document = build(reading)
    assert obs(document, "i", "document_kind").value == "credit_note"
    assert (obs(document, "a", "quantity").value, obs(document, "a", "line_total").value) == ("-1", "-12.00")
    assert obs(document, "i", "calculated_total").value == "-12.00" and "total_mismatch" not in codes(document)


def test_currency_symbol_never_fixes_a_code_and_conflicts_are_flagged():
    reading = PageReading(page=1, doc_type="menu", records=[
        rec("menu", "m", currency=f("$")), rec("menu_item", "i", "m", name=f("Burger"), price=f("12,50 €"))])
    document = build(reading)
    assert obs(document, "m", "currency").value is None and obs(document, "m", "currency").alternatives == ["USD", "CAD", "AUD"]
    clash = PageReading(page=1, doc_type="menu", records=[
        rec("menu", "m", currency=f("EUR")), rec("menu_item", "i", "m", name=f("Burger"), price=f("12.50 $"))])
    assert "currency_conflict" in codes(build(clash), "review")


def test_menu_relationships_translations_and_detached_prices():
    document = build(EXAMPLES["menu"]())
    parents = {e.id: e.parent_id for e in document.entities}
    assert parents["p1.v1"] == parents["p1.v2"] == "p1.i2" and parents["p1.c1"] == "p1.b1" and parents["p1.x1"] == "p1.i1"
    assert obs(document, "i1", "name").raw == "Soupe à l'oignon" and obs(document, "i1", "translated_name").value == "French onion soup"
    assert obs(document, "c1", "item_ref").value == "p1.i1"       # resolved to the existing item, which stays in its section
    assert obs(document, "x1", "price").value == "1.50"
    assert not any(i.code == "price_and_variants" for i in document.issues)
    assert "item_without_price" not in codes(document)  # i2 has priced variants
    bad = PageReading(page=1, doc_type="menu", records=[
        rec("menu", "m"), rec("menu_item", "i", "m", price=f("9,00")),
        rec("menu_item", "j", "m", name=f("Tarte")),
        rec("bundle", "b", "m", name=f("Formule"), price=f("20")), rec("bundle_choice", "c", "b", name=f("Tarte"), item_ref=f("nope"))])
    broken = build(bad)
    assert "detached_price" in codes(broken, "review")
    assert obs(broken, "c", "item_ref").state == "ambiguous" and obs(broken, "c", "item_ref").value is None
    same = PageReading(page=1, doc_type="menu", records=[rec("menu", "m"), rec("menu_item", "i", "m", price=f("9"),
        name=f("Soup", language="en"), translated_name=f("Soupe", language="en"))])
    assert "translation_same_language" in codes(build(same), "review")


def test_menu_claims_require_explicit_evidence_and_crossed_out_prices_are_kept():
    reading = PageReading(page=1, doc_type="menu", records=[rec("menu", "m"), rec("menu_item", "i", "m", name=f("Tarte"),
        price=f("8,00"), crossed_out=f("barré"), replacement_price=f("9,00"),
        allergen_claim=FieldRead(raw="sans gluten", evidence=""), dietary_claim=f("végétarien", "Tarte (végétarien)"))])
    document = build(reading)
    assert obs(document, "i", "allergen_claim").state == "ambiguous" and obs(document, "i", "allergen_claim").value is None
    assert obs(document, "i", "dietary_claim").value == "végétarien"
    assert (obs(document, "i", "price").value, obs(document, "i", "replacement_price").value, obs(document, "i", "crossed_out").value) == ("8.00", "9.00", True)
    assert "crossed_out_reading" in codes(document, "info")


def test_recipe_exact_fractions_ranges_imprecise_quantities_and_missing_yield():
    document = build(EXAMPLES["recipe"]())
    assert (obs(document, "g1", "quantity").raw, obs(document, "g1", "quantity").value) == ("⅓", "1/3")
    assert obs(document, "g1", "unit").value == "cup" and "never converted" in obs(document, "g1", "unit").reason
    quantity = obs(document, "g3", "quantity")
    assert (quantity.value, quantity.state) == (None, "readable")
    assert (obs(document, "g3", "quantity_min").value, obs(document, "g3", "quantity_max").value) == ("2", "3")
    assert obs(document, "g3", "quantity_min").origin == "calculated"
    assert obs(document, "g4", "quantity").value is None and "imprecise" in obs(document, "g4", "quantity").reason
    assert obs(document, "p1", "duration_minutes").value == "35" and obs(document, "p1", "temperature_c").value == "180"
    assert "recipe_yield_missing" in codes(document, "info") and not any(o.field.startswith("yield") for o in document.observations)
    assert [e.id for e in document.entities if e.kind == "ingredient"] == ["p1.g1", "p1.g2", "p1.g3", "p1.g4"]


def test_recipe_yield_unit_embedded_units_and_conflicts():
    reading = PageReading(page=1, doc_type="recipe", records=[
        rec("recipe", "r", title=f("Pain"), yield_quantity=f("2"), yield_unit=f("kg"), yield_type=f("portions")),
        rec("ingredient", "a", "r", name=f("farine"), quantity=f("250 g")),
        rec("ingredient", "b", "r", name=f("eau"), quantity=f("2 l"), unit=f("ml")),
        rec("ingredient", "c", "r", name=f("sel"), quantity=f("3"), quantity_min=f("5"), quantity_max=f("4"))])
    document = build(reading)
    assert obs(document, "a", "unit").value == "g" and obs(document, "a", "unit").origin == "calculated"
    assert {"yield_type_conflict", "unit_conflict", "range_conflict"} <= set(codes(document, "review"))
    ambiguous_scale = PageReading(page=1, doc_type="recipe", records=[rec("recipe", "r", title=f("x")),
        rec("step", "s", "r", instruction=f("Cuire"), temperature_c=f("180°"), temperature_text=f("th. 6"))])
    scale = build(ambiguous_scale)
    assert obs(scale, "s", "temperature_c").value is None and obs(scale, "s", "temperature_c").alternatives == ["180 C", "180 F"]
    assert obs(scale, "s", "temperature_text").value == "th. 6"


def test_multi_page_scope_coverage_and_skipped_pages():
    ingested, run = example_context(3)
    ingested = ingested.model_copy(update={"pages": ingested.pages[:2], "skipped_pages": [3], "source_skipped_pages": [3]})
    pages = [PageReading(page=1, doc_type="invoice", records=EXAMPLES["invoice"]().records),
             PageReading(page=2, doc_type="invoice", records=[rec("line_item", "z", None, description=f("Sel"), quantity=f("1"), line_total=f("1,00"))])]
    document = assemble(ingested, pages, [1, 2, 3], run, "doc-test")
    assert document.scope == [1, 2, 3] and document.coverage.skipped_pages == [3] and document.coverage.processed_pages == [1, 2]
    assert document.validation == "incomplete" and not review_gate(document)["approvable"]
    assert [e.page for e in document.entities if e.id == "p2.z"] == [2] and obs(document, "z", "line_total").value == "1.00"
    # an explicit group of [2] leaves page 1 to its own document and records the source-level skip
    only = assemble(ingested, pages, [2], run, "doc-two")
    assert only.scope == [2] and only.validation == "incomplete" and only.coverage.source_skipped_pages == [3]
    assert {"missing_root", "orphan_record"} <= set(codes(only))


def test_suspected_cutoff_and_incomplete_pages_are_visible_and_block_approval():
    reading = EXAMPLES["invoice"]().model_copy(update={"suspected_cutoff": True})
    document = build(reading)
    assert document.coverage.suspected_capture and document.coverage.original_capture == "suspected_incomplete"
    assert "suspected_cutoff" in codes(document, "review") and document.validation == "issues"
    truncated = build(EXAMPLES["invoice"]().model_copy(update={"incomplete": True}))
    assert truncated.coverage.incomplete_pages == [1] and truncated.validation == "incomplete" and "page_incomplete" in codes(truncated)
    clean = build(EXAMPLES["invoice"]())
    assert clean.coverage.original_capture == "not_established" and not clean.coverage.suspected_capture


def test_absence_on_an_incomplete_page_is_not_trusted():
    reading = PageReading(page=1, doc_type="invoice", incomplete=True, records=[rec("invoice", "i", total=f("5,00"))])
    reading.records[0].fields["due_date"] = FieldRead(raw=None, state="not_present")
    document = build(reading)
    assert "absence_unverified" in codes(document, "info") and obs(document, "i", "due_date").state == "not_present"


def test_empty_and_unclassified_documents_cannot_look_successful():
    empty = PageReading(page=1, doc_type="unknown", incomplete=True,
                        issues=[{"code": "model_failed", "message": "no output", "page": 1}])
    document = build(empty)
    assert document.entities == [] and document.validation == "incomplete" and not review_gate(document)["approvable"]
    assert {"empty_document", "model_failed"} <= set(codes(document))
    unknown = build(PageReading(page=1, doc_type="unknown", records=[rec("unknown", "u", description=f("???"))]))
    assert unknown.doc_type == "unknown" and not review_gate(unknown)["approvable"]


def test_mixed_document_types_are_reported_not_merged():
    pages = [EXAMPLES["invoice"](1), EXAMPLES["menu"](2)]
    document = build(pages, pages=2)
    assert document.doc_type == "invoice" and "page_doc_type_mismatch" in codes(document, "review") and not any(e.page == 2 for e in document.entities)
    tie = build([PageReading(page=1, doc_type="invoice", records=[rec("invoice", "i", total=f("1"))]),
                 PageReading(page=2, doc_type="menu", records=[rec("menu", "m", venue_name=f("x"))])], pages=2)
    assert tie.doc_type == "unknown" and "doc_type_conflict" in codes(tie)


def test_scope_and_duplicate_readings_are_validated():
    with pytest.raises(ProcessingError) as error:
        build(EXAMPLES["invoice"](), scope=[2])
    assert error.value.code == "invalid_scope"
    with pytest.raises(ProcessingError) as error:
        build([EXAMPLES["invoice"](), EXAMPLES["invoice"]()])
    assert error.value.code == "duplicate_page_reading"
    document = build([EXAMPLES["invoice"](1), EXAMPLES["menu"](2)], pages=2, scope=[1])
    assert document.scope == [1] and all(e.page == 1 for e in document.entities)


def test_invalid_parent_links_cycles_and_orphans():
    reading = PageReading(page=1, doc_type="invoice", records=[
        rec("invoice", "i", total=f("1,00")),
        rec("line_item", "a", "ghost", description=f("A")),
        rec("tax", "t", "a", name=f("TVA")),        # tax under a line item is allowed
        rec("line_item", "b", "t", description=f("B"))])  # a line item cannot sit under a tax
    document = build(reading)
    parents = {e.id: e.parent_id for e in document.entities}
    assert parents["p1.a"] == "p1.i" and parents["p1.b"] == "p1.i" and parents["p1.t"] == "p1.a"
    assert codes(document).count("invalid_parent") == 2
    cyc = PageReading(page=1, doc_type="menu", records=[rec("menu", "m"), rec("section", "s1", "s2", name=f("A")), rec("section", "s2", "s1", name=f("B"))])
    assert "invalid_parent" in codes(build(cyc))


def test_untrusted_text_stays_inert_data():
    hostile = "=HYPERLINK(\"http://x\")'; DROP TABLE documents;--"
    reading = PageReading(page=1, doc_type="menu", records=[rec("menu", "m", venue_name=f(hostile)),
                          rec("menu_item", "i", "m", name=f(hostile), price=f("1"))])
    document = build(reading)
    assert obs(document, "m", "venue_name").raw == hostile and obs(document, "m", "venue_name").value == hostile


def test_large_documents_assemble_in_bounded_time():
    import time
    lines = [rec("line_item", f"l{i}", "i", description=f(f"Article {i}"), quantity=f("2"), unit_price=f("1,50"), line_total=f("3,00"))
             for i in range(1000)]
    reading = PageReading(page=1, doc_type="invoice", records=[rec("invoice", "i", currency=f("EUR"), subtotal=f("3 000,00"), total=f("3000")), *lines])
    ingested, run = example_context()
    started = time.monotonic()
    document = assemble(ingested, [reading], [1], run, "doc-big")
    assert time.monotonic() - started < 10 and len(document.entities) == 1001
    assert next(o for o in document.observations if o.id == "p1.i.calculated_total").value == "3000.00"


def test_mixed_language_readings_keep_language_and_multilingual_alternatives_verbatim():
    reading = PageReading(page=1, doc_type="recipe", records=[
        rec("recipe", "r", title=f("Carrot cake / Gâteau aux carottes", language="en")),
        rec("ingredient", "a", "r", name=f("sucre", language="fr"), quantity=f("2"),
            unit=FieldRead(raw="c. à s.", evidence="2 c. à s.", state="ambiguous", language="fr",
                           alternatives=["cuillère à soupe", "tablespoon", "cuillère à soupe", "tbsp"]))])
    document = build(reading)
    assert obs(document, "r", "title").language == "en" and obs(document, "a", "name").language == "fr"
    unit = obs(document, "a", "unit")
    assert unit.state == "ambiguous" and unit.value is None and unit.raw == "c. à s."
    assert unit.alternatives == ["cuillère à soupe", "tablespoon", "tbsp"]       # order kept, duplicates dropped
    assert obs(document, "r", "title").value == "Carrot cake / Gâteau aux carottes"
    bilingual = PageReading(page=1, doc_type="invoice", records=[
        rec("invoice", "i", document_kind=f("Credit note / Avoir"), total=f("-5,00")),
        rec("line_item", "a", "i", description=f("Bon de livraison BL-12"), quantity=f("2 cartons"), unit_price=f("2,50"), line_total=f("-5,00"))])
    inv = build(bilingual)
    assert obs(inv, "i", "document_kind").value == "credit_note"
    assert (obs(inv, "a", "quantity").value, obs(inv, "a", "purchase_unit").value, obs(inv, "a", "purchase_unit").origin) == ("2", "carton", "calculated")


def test_invoice_consistency_checks_each_report_their_own_code():
    reading = PageReading(page=1, doc_type="invoice", records=[
        rec("invoice", "i", currency=f("EUR"), issue_date=f("15/04/2026"), due_date=f("01/04/2026"), subtotal=f("20,00"),
            tax_total=f("3,00"), charge_total=f("5,00"), discount_total=f("2,00"), total=f("26,00")),
        rec("line_item", "a", "i", description=f("A"), quantity=f("1"), unit_price=f("10,00"), discount=f("1,00"),
            discount_rate=f("20 %"), line_total=f("9,00")),
        rec("line_item", "b", "i", description=f("B"), quantity=f("1"), purchase_unit=f("kg"), pack_count=f("6"),
            unit_price=f("10,00"), line_total=f("10,00")),
        rec("tax", "t", "i", name=f("TVA"), basis=f("10,00"), rate=f("20 %"), amount=f("2,50")),
        rec("charge", "c", "i", name=f("Transport"), amount=f("4,00")),
        rec("discount", "d", "i", name=f("Remise"), amount=f("-1,00"))])
    document = build(reading)
    review = set(codes(document, "review"))
    assert {"due_before_issue", "subtotal_mismatch", "tax_amount_mismatch", "tax_sum_mismatch", "charge_sum_mismatch",
            "discount_sum_mismatch", "line_discount_conflict"} <= review
    assert "pack_definition_incomplete" in codes(document, "info")
    assert obs(document, "i", "calculated_total").value == "26.00"   # 20 + 3 + 5 - 2, printed aggregates preferred, subtotal as printed
    assert "total_mismatch" not in review
    conflict = PageReading(page=1, doc_type="invoice", records=[rec("invoice", "i"), rec("line_item", "b", "i", description=f("B"),
        quantity=f("1"), purchase_unit=f("kg"), pack_count=f("6"), pack_unit_quantity=f("1"), pack_unit=f("l"))])
    assert "pack_purchase_unit_conflict" in codes(build(conflict), "review")


def test_aggregates_fall_back_to_child_rows_and_total_checks_can_be_indeterminate():
    reading = PageReading(page=1, doc_type="invoice", records=[
        rec("invoice", "i", currency=f("EUR"), total=f("12,00")),
        rec("line_item", "a", "i", description=f("A"), quantity=f("1"), unit_price=f("10,00"), line_total=f("10,00")),
        rec("tax", "t", "i", name=f("TVA"), amount=f("2,00"))])
    document = build(reading)
    assert obs(document, "i", "calculated_total").value == "12.00" and "total_mismatch" not in codes(document)
    lone = build(PageReading(page=1, doc_type="invoice", records=[rec("invoice", "i", total=f("12,00"))]))
    assert "total_check_indeterminate" in codes(lone, "info") and not any(o.origin == "calculated" for o in lone.observations)
    partial = build(PageReading(page=1, doc_type="invoice", records=[rec("invoice", "i", total=f("12,00")),
        rec("line_item", "a", "i", description=f("A"), quantity=f("1"), unit_price=f("10,00"))]))
    assert "total_check_indeterminate" in codes(partial, "info")
