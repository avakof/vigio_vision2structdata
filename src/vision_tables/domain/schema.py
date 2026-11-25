"""Machine-readable controlled vocabulary for the three document families.

The field *names* come from the coordinator-owned ``vision_tables.vocabulary`` (the wire contract).
This module adds what adapters and the domain need beyond names: lexical types, which fields are
critical, containment (allowed parents), and short model-facing hints. ``vocabulary()`` is JSON-able
and ``model_vocabulary(doc_type)`` is the compact projection meant for prompts and grammars.
Consistency with the wire vocabulary is verified at import time.
"""
from __future__ import annotations

from ..vocabulary import CALCULATED_ONLY, FAMILIES, FIELDS, ROOTS

VOCABULARY_VERSION = "1"
ROOT_KIND = {"invoice": "invoice", "menu": "menu", "recipe": "recipe", "unknown": "unknown"}

# Lexical types of the *normalized* value (Observation.value). Raw text is always kept separately.
LEXICAL_TYPES = {
    "text": "NFC text, whitespace collapsed, case and accents preserved",
    "money": "signed finite decimal string using '.' (printed digits kept, e.g. '8.50'); sign as printed",
    "number": "signed decimal string, or reduced exact fraction 'n/d' when the decimal does not terminate",
    "quantity": "number, or null with a reason for a range / imprecise quantity ('to taste')",
    "percent": "decimal string in percentage points, 0..100 ('5.5' for 5,5 %)",
    "count": "positive integer string",
    "date": "ISO 8601 calendar date; null (state ambiguous) when day/month order or century is unresolved",
    "currency": "ISO 4217 code; null when only an ambiguous symbol such as '$' is printed",
    "flag": "strict boolean",
    "unit": "canonical unit code (g, kg, ml, l, tbsp, carton ...); null when unrecognized",
    "price_basis": "canonical unit code the unit price is per ('piece' for each)",
    "duration": "minutes as a number string (decimal, or exact fraction)",
    "temp_c": "degrees Celsius as a number string; the scale must be printed",
    "temp_f": "degrees Fahrenheit as a number string; the scale must be printed",
    "ref": "page-local record id of an existing menu_item, resolved to its occurrence id",
    "enum:document_kind": "invoice | credit_note | receipt | delivery_note | quote | unknown",
    "enum:yield_type": "portions | mass | volume | count",
}
ENUMS = {
    "document_kind": ("invoice", "credit_note", "receipt", "delivery_note", "quote", "unknown"),
    "yield_type": ("portions", "mass", "volume", "count"),
}

_T = {
    "invoice": "document_kind=enum:document_kind document_number=text supplier_name=text supplier_id=text supplier_address=text "
               "customer_name=text customer_id=text customer_address=text issue_date=date due_date=date currency=currency "
               "subtotal=money total=money tax_total=money discount_total=money charge_total=money calculated_total=money",
    "line_item": "description=text sku=text quantity=number purchase_unit=unit pack_count=count pack_unit_quantity=number "
                 "pack_unit=unit pack_total_quantity=number unit_price=money price_basis=price_basis discount=money "
                 "discount_rate=percent tax=money tax_rate=percent line_total=money calculated_line_total=money currency=currency",
    "tax": "name=text basis=money rate=percent amount=money currency=currency",
    "charge": "name=text amount=money currency=currency",
    "discount": "name=text rate=percent amount=money currency=currency",
    "menu": "venue_name=text version=text date=date currency=currency",
    "section": "name=text translated_name=text description=text",
    "menu_item": "name=text translated_name=text description=text translated_description=text price=money currency=currency "
                 "dietary_claim=text allergen_claim=text crossed_out=flag replacement_price=money",
    "variant": "name=text size=text price=money currency=currency crossed_out=flag replacement_price=money",
    "supplement": "name=text price=money currency=currency",
    "bundle": "name=text translated_name=text description=text price=money currency=currency",
    "bundle_choice": "name=text group=text description=text item_ref=ref",
    "recipe": "title=text version=text yield_quantity=quantity yield_unit=unit yield_type=enum:yield_type description=text",
    "subrecipe": "name=text title=text yield_quantity=quantity yield_unit=unit",
    "ingredient": "name=text quantity=quantity quantity_text=text quantity_min=number quantity_max=number unit=unit "
                  "preparation=text optional=flag crossed_out=flag replacement_quantity=quantity",
    "substitution": "name=text quantity=quantity unit=unit preparation=text",
    "step": "instruction=text duration_minutes=duration time_text=text temperature_c=temp_c temperature_f=temp_f temperature_text=text",
    "unknown": "description=text",
}
FIELD_TYPES = {k: dict(p.split("=") for p in v.split()) for k, v in _T.items()}

# Fields whose unresolved reading blocks approval (a reviewer can resolve them by editing the observation).
CRITICAL = {
    "invoice": "total issue_date currency",
    "line_item": "description quantity unit_price line_total",
    "tax": "rate amount", "charge": "amount", "discount": "amount",
    "menu_item": "name price", "variant": "price", "supplement": "price", "bundle": "price",
    "bundle_choice": "name", "section": "name",
    "recipe": "title", "ingredient": "name quantity", "step": "instruction",
    "unknown": "description",
}
CRITICAL_FIELDS = {k: frozenset(v.split()) for k, v in CRITICAL.items()}
# Critical fields whose complete absence from the reading needs a reviewer's look (not an edit).
EXPECTED_ON_ROOT = {"invoice": ("document_number", "supplier_name", "issue_date", "currency", "total"),
                    "menu": ("venue_name",), "recipe": ("title",), "unknown": ()}

# Allowed parent kinds (typed containment). A missing parent of a JOIN_ROOT kind is joined to the
# document's single root of its family; REQUIRED_PARENT kinds without a parent are flagged.
PARENTS = {
    "invoice": (), "menu": (), "recipe": (), "unknown": (),
    "line_item": ("invoice",), "tax": ("invoice", "line_item"), "charge": ("invoice", "line_item"),
    "discount": ("invoice", "line_item"),
    "section": ("menu",), "menu_item": ("section", "menu"), "variant": ("menu_item",),
    "supplement": ("menu_item", "section", "menu"), "bundle": ("section", "menu"), "bundle_choice": ("bundle",),
    "subrecipe": ("recipe",), "ingredient": ("recipe", "subrecipe"), "substitution": ("ingredient",),
    "step": ("recipe", "subrecipe"),
}
REQUIRED_PARENT = frozenset("line_item tax charge discount variant bundle_choice substitution subrecipe ingredient step".split())
JOIN_ROOT = frozenset("line_item tax charge discount section subrecipe ingredient step".split())

HINTS = {
    "raw": "copy the printed/handwritten text exactly as seen (accents, decimal commas, symbols); never calculate",
    "evidence": "short exact quote from the page that supports the reading",
    "state": "readable | ambiguous (give alternatives or a reason) | unreadable (give a reason) | not_present",
    "dates": "keep '03/04/2026' as printed; do not choose day/month order",
    "currency": "copy the symbol or code as printed; '$' alone is not a currency",
    "crossed_out": "a struck price/quantity is its own reading; put the handwritten replacement in replacement_*",
    "translations": "use translated_* only when the translation is printed beside the original; never translate yourself",
    "totals": "printed totals go in total/subtotal/...; calculated_* and pack_total_quantity are domain-only",
    "relations": "set parent_id to the local_id of the containing record on the same page; omit it for continuation pages",
}


def _check_against_wire() -> None:
    assert set(FIELD_TYPES) == set(FIELDS), "schema kinds drifted from vision_tables.vocabulary"
    for kind, fields in FIELDS.items():
        assert tuple(FIELD_TYPES[kind]) == fields, f"field list drifted for {kind}"
    assert set(PARENTS) == set(FIELDS) and set(CRITICAL_FIELDS) <= set(FIELDS)
    assert {k for k in ROOT_KIND.values() if k != "unknown"} == ROOTS
    assert all(set(v) <= set(FIELDS) for v in PARENTS.values())
    assert all(f in FIELD_TYPES[k] for k, fs in CRITICAL_FIELDS.items() for f in fs)
    assert set(FAMILIES) == set(ROOT_KIND)


_check_against_wire()


def is_calculated(kind: str, field: str) -> bool:
    return field in CALCULATED_ONLY and field in FIELD_TYPES.get(kind, {})


def vocabulary() -> dict:
    """Complete JSON-able vocabulary (schema version, families, kinds, parents, field types)."""
    kinds = {}
    for kind, fields in FIELD_TYPES.items():
        kinds[kind] = {
            "parents": list(PARENTS[kind]), "parent_required": kind in REQUIRED_PARENT,
            "joins_root": kind in JOIN_ROOT,
            "fields": {name: {"type": typ, "critical": name in CRITICAL_FIELDS.get(kind, ()),
                              "model_observable": name not in CALCULATED_ONLY}
                       for name, typ in fields.items()},
        }
    return {"version": VOCABULARY_VERSION,
            "doc_types": {t: {"root": ROOT_KIND[t], "kinds": list(FAMILIES[t])} for t in FAMILIES},
            "kinds": kinds, "lexical_types": dict(LEXICAL_TYPES),
            "enums": {k: list(v) for k, v in ENUMS.items()}, "hints": dict(HINTS),
            "calculated_only": sorted(CALCULATED_ONLY)}


def model_vocabulary(doc_type: str) -> dict:
    """Compact projection for one document family: only fields a model may observe."""
    if doc_type not in FAMILIES:
        raise ValueError(f"unknown document type {doc_type!r}")
    return {"doc_type": doc_type, "version": VOCABULARY_VERSION, "root": ROOT_KIND[doc_type],
            "records": {kind: {"parent": list(PARENTS[kind]),
                               "fields": {n: t for n, t in FIELD_TYPES[kind].items() if n not in CALCULATED_ONLY}}
                        for kind in FAMILIES[doc_type]},
            "rules": [HINTS[k] for k in ("raw", "evidence", "state", "relations")]}


if __name__ == "__main__":  # python -m vision_tables.domain.schema > vocabulary.json
    import json
    print(json.dumps(vocabulary(), ensure_ascii=False, indent=2, sort_keys=True))
