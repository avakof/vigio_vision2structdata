"""Issue catalog: codes, severities and how a reviewer resolves each.

Severity (``Issue.severity``): ``review`` issues are unresolved and block human approval; ``info`` issues
are retained notes that approval does not clear. Every ``review`` code the domain raises is resolvable by
editing an existing observation (or by reprocessing), except those marked ``reprocess``.

``DERIVED`` codes are recomputed from the observations on every revalidation. All other codes
(ingestion, adapter and assembly issues) are carried forward unchanged.
"""
from __future__ import annotations

from ..contracts import Issue

# code -> (default severity, applies to doc types ("*" = all), how it is resolved)
CATALOG = {
    # unresolved readings (derived: disappear when a human supplies a readable value)
    "ambiguous_reading": ("review", "*", "Correct the observation to the value printed on the page."),
    "unreadable_field": ("review", "*", "Correct the observation to the value printed on the page."),
    # evidence
    "ungrounded_reading": ("review", "*", "Correct the observation after checking the page; the model gave no supporting text."),
    "evidence_mismatch": ("review", "*", "Check the quoted evidence against the raw reading and correct the observation."),
    "evidence_not_in_region": ("review", "*", "Check the cited region and correct the observation."),
    "invalid_evidence": ("review", "*", "Check the page and correct the observation; the cited region was not valid."),
    # arithmetic (invoice)
    "line_total_mismatch": ("review", "invoice", "Correct the misread quantity, price, discount or line total."),
    "line_discount_conflict": ("review", "invoice", "Correct the discount amount or rate."),
    "subtotal_mismatch": ("review", "invoice", "Correct the misread line total or subtotal."),
    "tax_amount_mismatch": ("review", "invoice", "Correct the tax base, rate or amount."),
    "tax_sum_mismatch": ("review", "invoice", "Correct the tax lines or tax total."),
    "charge_sum_mismatch": ("review", "invoice", "Correct the charge lines or charge total."),
    "discount_sum_mismatch": ("review", "invoice", "Correct the discount lines or discount total."),
    "total_mismatch": ("review", "invoice", "Correct the misread component or the printed total."),
    "pack_purchase_unit_conflict": ("review", "invoice", "Correct the purchase unit or the pack definition."),
    "due_before_issue": ("review", "invoice", "Correct the issue or due date."),
    "currency_conflict": ("review", "*", "Correct the currency or the amount that prints another currency."),
    "unit_conflict": ("review", "*", "Correct the unit; the quantity text prints a different one."),
    "range_conflict": ("review", "recipe", "Correct quantity_min, quantity_max or the quantity text."),
    "total_consistent_without_tax": ("info", "invoice", "Confirm whether tax is included in the printed prices; nothing is inferred."),
    "line_total_matches_with_tax": ("info", "invoice", "Confirm whether line totals include tax; nothing is inferred."),
    "rounding_policy_difference": ("info", "invoice", "The printed total matches rounding once at the end, not per line."),
    "line_check_indeterminate": ("info", "invoice", "The line cannot be checked: price basis and quantity unit are not comparable."),
    "total_check_indeterminate": ("info", "invoice", "The total cannot be checked: components are missing or unresolved."),
    "pack_definition_incomplete": ("info", "invoice", "No pack total is derived without every evidenced pack fact."),
    # menu
    "detached_price": ("review", "menu", "Correct the item name or remove the price reading by marking it not_present."),
    "invalid_item_ref": ("review", "menu", "Correct item_ref to an existing menu item."),
    "translation_same_language": ("review", "menu", "Confirm the translation pairing; both readings carry the same language."),
    "replacement_without_crossout": ("review", "*", "Mark the original crossed_out or remove the replacement reading."),
    "translation_without_layout_evidence": ("info", "menu", "The pairing has no region evidence; confirm the translation belongs to this dish."),
    "identical_translation": ("info", "menu", "The translated text equals the original."),
    "item_without_price": ("info", "menu", "No price was read for this item."),
    "price_and_variants": ("info", "menu", "The item has its own price and priced variants."),
    "crossed_out_reading": ("info", "*", "A struck reading is kept with its replacement; confirm which is current."),
    # recipe
    "yield_type_conflict": ("review", "recipe", "Correct the yield unit or type."),
    "recipe_yield_missing": ("info", "recipe", "No yield was read; none is invented and no per-portion figure is derived."),
    # structure / generic
    "unsupported_kind": ("review", "*", "reprocess: model output used an entity kind outside the vocabulary; it was dropped."),
    "unsupported_field": ("review", "*", "reprocess: model output used a field outside the vocabulary; it was dropped."),
    "page_doc_type_mismatch": ("review", "*", "reprocess or regroup pages: the page is another document type."),
    "doc_type_conflict": ("review", "*", "reprocess or regroup pages: pages disagree on the document type."),
    "unclassified_page": ("review", "*", "reprocess: the page could not be classified."),
    "duplicate_local_id": ("info", "*", "A page-local id was reused; the later record was renamed."),
    "invalid_parent": ("info", "*", "A record's parent link was invalid and was ignored."),
    "parent_joined": ("info", "*", "Records without a parent were joined to the document's single root."),
    "orphan_record": ("review", "*", "reprocess: a record that needs a parent has none."),
    "missing_root": ("info", "*", "No root record was read on the processed pages."),
    "multiple_roots": ("info", "*", "Several root records were read; unparented rows were not joined."),
    "critical_missing": ("info", "*", "An expected field was not read; confirm it is absent from the page."),
    "duplicate_row": ("info", "*", "Identical rows were kept as distinct records."),
    "absence_unverified": ("info", "*", "'not_present' was reported on an incomplete page."),
    "page_not_read": ("review", "*", "reprocess: no reading exists for this page."),
    "page_not_ingested": ("review", "*", "reprocess: this page was not ingested."),
    "page_incomplete": ("review", "*", "reprocess: the page reading is incomplete."),
    "suspected_cutoff": ("review", "*", "reprocess from a complete capture; a suspected cutoff cannot be approved."),
    "empty_document": ("review", "*", "reprocess: nothing was read from the processed pages."),
    "region_conflict": ("info", "*", "Two regions shared an id; the ingested region was kept."),
    "invalid_region": ("review", "*", "A region was outside its page or on an unknown page and was dropped."),
}
DERIVED = frozenset(code for code in CATALOG if code in {
    "ambiguous_reading", "unreadable_field", "ungrounded_reading", "evidence_mismatch", "evidence_not_in_region",
    "line_total_mismatch", "line_discount_conflict", "subtotal_mismatch", "tax_amount_mismatch", "tax_sum_mismatch",
    "charge_sum_mismatch", "discount_sum_mismatch", "total_mismatch", "pack_purchase_unit_conflict", "due_before_issue",
    "currency_conflict", "unit_conflict", "range_conflict", "total_consistent_without_tax", "line_total_matches_with_tax",
    "rounding_policy_difference", "line_check_indeterminate", "total_check_indeterminate", "pack_definition_incomplete",
    "detached_price", "invalid_item_ref", "translation_same_language", "replacement_without_crossout",
    "translation_without_layout_evidence", "identical_translation", "item_without_price", "price_and_variants",
    "crossed_out_reading", "yield_type_conflict", "recipe_yield_missing", "critical_missing", "duplicate_row",
    "absence_unverified", "missing_root", "multiple_roots"})
# Carried issues about one observation retire when a human supplies that observation.
OBSERVATION_BOUND = frozenset({"invalid_evidence"})


def make(code: str, message: str, *, page: int | None = None, observations=(), severity: str | None = None) -> Issue:
    return Issue(code=code, message=message, page=page, observation_ids=list(dict.fromkeys(observations)),
                 severity=severity or CATALOG[code][0])


def review_gates() -> dict:
    """Per document type: the codes that can block approval and how a reviewer resolves each."""
    return {doc_type: {code: spec[2] for code, spec in CATALOG.items()
                       if spec[0] == "review" and spec[1] in ("*", doc_type)}
            for doc_type in ("invoice", "menu", "recipe", "unknown")}
