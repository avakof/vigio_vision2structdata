"""A reviewer can supply omitted facts without rewriting the extracted version."""
import csv
from io import StringIO

import pytest
from vision_tables.config import ProcessingError
from vision_tables.contracts import CorrectionSet, Document
from vision_tables.domain import apply_corrections, assemble
from vision_tables.domain.examples import example_context, invoice_page
from vision_tables.exports import render_tables
from vision_tables.store import Store


def test_row_and_field_add_remove_preserves_original_and_row_order(tmp_path):
    ingestion, run = example_context()
    store = Store(tmp_path)
    job = store.create_job(kind="extract", input_path="fixture", output_dir=str(tmp_path / "out"), settings_json="{}")
    run.job_id = job.job_id
    base = assemble(ingestion, [invoice_page()], [1], run, "test")
    store.persist_candidates(job.job_id, job.token, [base])
    payload = base.model_dump_json()
    addition = {
        "op": "add_row", "entity_id": "human-line", "page": 1, "kind": "line_item",
        "parent_id": "p1.inv", "after_entity_id": "p1.l1", "reason": "Omitted printed line",
        "fields": {"description": {"raw": "Café", "value": "Café"},
                   "line_total": {"raw": "7,50", "value": "7.50"}},
    }
    field = {"op": "add_field", "entity_id": "p1.inv", "field": "customer_id",
             "reason": "ID on the heading", "reading": {"raw": "CX-651", "value": "CX-651"}}
    event = CorrectionSet(document_id="test", base_version=1, reviewer="R", structure=[addition, field])
    edited = store.apply_corrections("test", event).document
    assert store.apply_corrections("test", event).replayed
    assert store.get_document("test", 1) == base
    rows = list(csv.DictReader(StringIO(render_tables(edited)["structural_corrections.csv"].decode())))
    assert [(r["operation"], r["entity_id"]) for r in rows] == [("add_row", "human-line"), ("add_field", "p1.inv")]
    assert base.model_dump_json() == payload
    ids = [e.id for e in edited.entities]
    assert ids.index("human-line") == ids.index("p1.l1") + 1
    amount = next(o for o in edited.observations if o.id == "human-line.line_total")
    assert (amount.raw, amount.value, amount.origin) == ("7,50", "7.50", "human_supplied")
    assert amount.evidence[0].page == 1
    assert Document.model_validate_json(edited.model_dump_json()) == edited
    deleted = apply_corrections(edited, CorrectionSet(document_id="test", base_version=2, reviewer="R",
                                  structure=[{"op": "remove_row", "entity_id": "human-line", "reason": "Duplicate"}]))
    assert "human-line" not in {e.id for e in deleted.entities}
    assert all(o.entity_id != "human-line" for o in deleted.observations)
    assert "human-line.line_total" in {o.id for o in edited.observations}
    assert deleted.version == 3
    with pytest.raises(ProcessingError):
        apply_corrections(deleted, CorrectionSet(document_id="test", base_version=3, reviewer="R", structure=[addition]))
    with pytest.raises(ProcessingError):
        apply_corrections(base, CorrectionSet(document_id="test", base_version=1, reviewer="R",
                              structure=[field, {"op": "remove_row", "entity_id": "p1.inv", "reason": "Invalid"}]))
    assert base.model_dump_json() == payload
