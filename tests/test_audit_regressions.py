"""Counterexamples from the independent Gate 2 review."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from vision_tables.contracts import Correction, CorrectionSet, Document, ModelPayload
from vision_tables.wire_json import parse_object


@pytest.fixture
def candidate():
    sha = "a" * 64
    return {
        "document_id": "doc", "scope": [1], "doc_type": "invoice", "version": 1,
        "source": {"sha256": sha, "size_bytes": 123, "media_type": "image/png",
                   "page_count": 1, "original_path": "original.png"},
        "pages": [{"number": 1, "width": 12, "height": 10, "unit": "px", "image_path": "p1.png",
                   "image_width": 12, "image_height": 10, "image_to_original": [1, 0, 0, 1, 0, 0]}],
        "regions": [], "run": {"job_id": "job1", "pipeline": "direct", "model": "example",
                               "revision": "abc", "runtime": {}, "settings": {}, "config_hash": sha},
        "coverage": {"processed_pages": [1]}, "entities": [{"id": "i", "kind": "invoice", "page": 1, "ordinal": 0}],
        "observations": [{"id": "o1", "entity_id": "i", "field": "total", "raw": "8.50",
                          "value": "8.50", "state": "readable", "evidence": [{"page": 1, "quote": "8.50"}]}],
        "issues": [], "validation": "valid",
    }


def test_candidate_starts_in_review_and_needs_a_reviewer_event(candidate):
    assert Document.model_validate(candidate).approval == "needs_review"
    with pytest.raises(ValidationError):
        Document.model_validate(dict(candidate, approval="human_approved"))
    approval = {"reviewer": "Jane", "document_id": "doc", "base_version": 1, "approve": True}
    assert CorrectionSet.model_validate(approval).changes == []
    assert Document.model_validate(dict(candidate, version=2, parent_version=1, approval="human_approved",
                                        corrections=[approval])).approval == "human_approved"


def test_contradictory_claims_are_rejected(candidate):
    for update in (
        {"version": 2, "parent_version": 3},
        {"observations": candidate["observations"] + [dict(candidate["observations"][0], id="o2")]},
        {"coverage": {"processed_pages": [1], "original_capture": "suspected_incomplete"}},
        {"coverage": {"processed_pages": [], "skipped_pages": [1], "incomplete_pages": [1]}},
        {"approval": "human_approved", "observations": []},
    ):
        with pytest.raises(ValidationError):
            Document.model_validate(dict(candidate, **update))
    for update in ({"value": None, "raw": None}, {"field": "random"},
                   {"state": "ambiguous", "value": None}, {"evidence": [{"page": 2}]}):
        snapshot = deepcopy(candidate)
        snapshot["observations"][0].update(update)
        with pytest.raises(ValidationError):
            Document.model_validate(snapshot)


def test_strict_json_and_semantic_boundaries():
    for text in ('{"field":{"raw":"8.50","raw":"6.50"}}', '{} {}', '{"n":NaN}', '{"n":1e400}'):
        with pytest.raises(ValueError):
            parse_object(text)
    with pytest.raises(ValidationError):
        Correction(observation_id="o", value=1, reason="edit")
    with pytest.raises(ValidationError):
        CorrectionSet(reviewer="Sam", document_id="doc", base_version=1,
                      changes=[{"observation_id": "x", "value": "8", "reason": "recheck"}] * 2)
    with pytest.raises(ValidationError):
        ModelPayload(doc_type="invoice", records=[], regions=[{"page": 1}])
