import json
import re
from pathlib import Path

import vision_tables.domain as domain
from vision_tables.domain import catalog, model_vocabulary, vocabulary
from vision_tables.domain.schema import CRITICAL_FIELDS, FIELD_TYPES, PARENTS
from vision_tables.vocabulary import CALCULATED_ONLY, FAMILIES, FIELDS, ROOTS


def test_vocabulary_is_json_stable_and_matches_the_wire_contract():
    first = json.dumps(vocabulary(), sort_keys=True)
    assert first == json.dumps(vocabulary(), sort_keys=True)
    full = vocabulary()
    assert set(full["kinds"]) == set(FIELDS) and set(full["doc_types"]) == set(FAMILIES)
    for kind, spec in full["kinds"].items():
        assert tuple(spec["fields"]) == FIELDS[kind]
        assert set(spec["parents"]) <= set(FIELDS)
        assert {name for name, f in spec["fields"].items() if not f["model_observable"]} <= CALCULATED_ONLY
    assert {full["doc_types"][t]["root"] for t in ROOTS} == ROOTS


def test_model_projection_is_compact_and_excludes_calculated_fields():
    for doc_type in ("invoice", "menu", "recipe"):
        projection = model_vocabulary(doc_type)
        assert projection["root"] == doc_type and list(projection["records"]) == list(FAMILIES[doc_type])
        fields = {n for rec in projection["records"].values() for n in rec["fields"]}
        assert not fields & CALCULATED_ONLY
        assert len(json.dumps(projection)) < 4000
    invoice = model_vocabulary("invoice")["records"]
    assert invoice["line_item"]["parent"] == ["invoice"] and invoice["line_item"]["fields"]["quantity"] == "number"
    assert domain.FIELD_TYPES is FIELD_TYPES


def test_every_kind_is_reachable_and_critical_fields_exist():
    for doc_type, kinds in FAMILIES.items():
        for kind in kinds:
            assert all(p in kinds for p in PARENTS[kind])
    for kind, fields in CRITICAL_FIELDS.items():
        assert fields <= set(FIELDS[kind])


def test_every_issue_code_used_in_the_domain_is_cataloged_with_a_resolution():
    source = "\n".join(p.read_text() for p in Path(domain.__file__).parent.glob("*.py") if p.name != "catalog.py")
    used = set(re.findall(r'(?:note|make)\(\s*"([a-z_]+)"', source))
    assert used and used <= set(catalog.CATALOG), used - set(catalog.CATALOG)
    assert catalog.DERIVED <= set(catalog.CATALOG)
    for doc_type, gates in catalog.review_gates().items():
        assert gates and all(text for text in gates.values())
    assert "total_mismatch" in catalog.review_gates()["invoice"] and "total_mismatch" not in catalog.review_gates()["menu"]
    assert "detached_price" in catalog.review_gates()["menu"] and "yield_type_conflict" in catalog.review_gates()["recipe"]
