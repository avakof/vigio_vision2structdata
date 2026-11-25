"""Shared builders for store/CLI tests: synthetic candidates, a pipeline stand-in and a domain stand-in.

The real domain module belongs to another worktree. ``fake_apply`` implements only the behaviour the
store relies on (typed edits, calculated-field rejection, recomputation, history, approval reset); the
store's own checks are what the tests exercise.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

from vision_tables.artifacts import write_json
from vision_tables.config import ProcessingError, Settings, digest
from vision_tables.contracts import CorrectionSet, Document

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic page bytes"


def build_document(job_id: str, settings: Settings, *, source_bytes: bytes = PNG, rows=(("Flour", "2.00"), ("Flour", "2.00")),
                   printed_total="4.00", supplier="Moulin Rouge", number="F-001", incomplete=False,
                   formula: str | None = None, config_salt: str = "") -> Document:
    sha = hashlib.sha256(source_bytes).hexdigest()
    scope = [1, 2] if incomplete else [1]
    document_id = "doc-" + digest([sha, scope])[:32]
    config_hash = digest({"settings": settings.content_settings(), "salt": config_salt})

    def ev(quote):
        return [{"page": 1, "region_id": "r1", "quote": quote, "channel": "direct"}]

    entities = [{"id": "inv", "kind": "invoice", "page": 1, "ordinal": 0}]
    obs = []

    def add(oid, entity, field, raw, value, quote=None):
        obs.append({"id": oid, "entity_id": entity, "field": field, "raw": raw, "value": value, "state": "readable",
                    "evidence": ev(quote or raw)})
    add("o-sup", "inv", "supplier_name", supplier, supplier)
    add("o-num", "inv", "document_number", number, number)
    add("o-total", "inv", "total", formula or printed_total, printed_total)
    line_ids = []
    for i, (name, price) in enumerate(rows):
        entities.append({"id": f"li{i}", "kind": "line_item", "parent_id": "inv", "page": 1, "ordinal": i + 1})
        add(f"o-d{i}", f"li{i}", "description", name, name)
        add(f"o-p{i}", f"li{i}", "line_total", price, price)
        line_ids.append(f"o-p{i}")
    calc = sum((Decimal(p) for _, p in rows), Decimal(0))
    obs.append({"id": "o-calc", "entity_id": "inv", "field": "calculated_total", "raw": None, "value": str(calc),
                "state": "readable", "origin": "calculated", "evidence": [],
                "derivation": {"calculation": "sum(line_total)", "inputs": line_ids}})
    issues = [] if Decimal(printed_total) == calc else [
        {"code": "total_mismatch", "message": "Printed total differs from the sum of line totals.",
         "observation_ids": ["o-total", "o-calc"]}]
    if incomplete:
        issues.append({"code": "page_skipped", "message": "Page 2 was skipped by the page budget.", "page": 2})
    cov = {"processed_pages": [1], "skipped_pages": [2] if incomplete else [], "incomplete_pages": [],
           "source_skipped_pages": [2] if incomplete else []}
    return Document.model_validate({
        "document_id": document_id, "doc_type": "invoice", "scope": scope,
        "source": {"sha256": sha, "media_type": "image/png", "size_bytes": len(source_bytes),
                   "page_count": 2 if incomplete else 1, "original_path": "source/original.png"},
        "pages": [{"number": 1, "width": 100, "height": 200, "unit": "px", "image_path": "pages/0001.png",
                   "image_width": 100, "image_height": 200, "image_to_original": [1, 0, 0, 1, 0, 0]}],
        "regions": [{"id": "r1", "page": 1, "order": 0, "kind": "text", "text": "invoice",
                     "box": {"xyxy": [0, 0, 50, 50], "precision": "parser_region"}}],
        "run": {"job_id": job_id, "pipeline": settings.pipeline, "model": "fake", "revision": "r0",
                "runtime": {}, "settings": settings.content_settings(), "config_hash": config_hash},
        "coverage": cov, "entities": entities, "observations": obs, "issues": issues,
        "validation": "incomplete" if incomplete else ("valid" if not issues else "issues"),
    })


def fake_extract(*, source_bytes: bytes = PNG, **build):
    """A pipeline.extract replacement that writes real job artifacts like the lead's pipeline."""
    calls = []

    def extract(path, output, settings, *, job_id=None):
        calls.append(Path(path))
        root = settings.state_dir.resolve() / "jobs" / job_id
        root.mkdir(mode=0o700, parents=True, exist_ok=False)
        data = Path(path).read_bytes() if Path(path).exists() and source_bytes is None else source_bytes
        (root / "source").mkdir()
        (root / "source/original.png").write_bytes(data)
        (root / "pages").mkdir()
        (root / "pages/0001.png").write_bytes(PNG)
        write_json(root / "job.json", {"job_id": job_id, "status": "running", "stage": "read"})
        return [build_document(job_id, settings, source_bytes=data, **build)]
    extract.calls = calls
    return extract


def fake_apply(document: Document, changes: CorrectionSet) -> Document:
    if changes.base_version != document.version or changes.document_id != document.document_id:
        raise ValueError("corrections target a different version")
    data = document.model_dump(mode="json")
    by_id = {o["id"]: o for o in data["observations"]}
    for change in changes.changes:
        target = by_id.get(change.observation_id)
        if target is None:
            raise ValueError(f"unknown observation {change.observation_id}")
        if target["origin"] == "calculated":
            raise ValueError("calculated observations cannot be edited directly")
        target.update(raw=change.raw if change.raw is not None else target["raw"], value=change.value,
                      state=change.state, origin="human_supplied", reason=change.reason,
                      evidence=target["evidence"] or [{"page": 1, "quote": "", "channel": "human"}])
    if not any(o["state"] != "readable" for o in data["observations"]):
        lines = [Decimal(o["value"]) for o in data["observations"] if o["field"] == "line_total" and o["value"]]
        by_id["o-calc"]["value"] = str(sum(lines, Decimal(0)))
    total = by_id["o-total"]["value"]
    data["issues"] = [i for i in data["issues"] if i["code"] != "total_mismatch"]
    if total is not None and by_id["o-calc"]["value"] and Decimal(total) != Decimal(by_id["o-calc"]["value"]):
        data["issues"].append({"code": "total_mismatch", "message": "Printed total differs.",
                               "observation_ids": ["o-total", "o-calc"]})
    if document.validation != "incomplete":  # coverage problems are not cleared by editing a reading
        data["validation"] = "valid" if not data["issues"] else "issues"
    data["corrections"].append(changes.model_dump(mode="json"))
    data["version"], data["parent_version"] = document.version + 1, document.version
    data["approval"] = "needs_review"
    if changes.approve:
        if data["validation"] != "valid":
            raise ValueError("unresolved issues block approval")
        data["approval"] = "human_approved"
    return Document.model_validate(data)


def correction(document_id: str, base: int, obs="o-p0", value="3.00", *, reviewer="Reviewer A", approve=False):
    return CorrectionSet.model_validate({
        "document_id": document_id, "base_version": base, "reviewer": reviewer, "approve": approve,
        "changes": [{"observation_id": obs, "value": value, "raw": value, "reason": "checked the printed row"}]
        if obs else []})


def raises(code):
    import pytest
    return pytest.raises(ProcessingError, match=".*") if code is None else _Code(code)


class _Code:
    def __init__(self, code):
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, kind, exc, tb):
        assert kind is not None and issubclass(kind, ProcessingError), f"expected ProcessingError {self.code}"
        assert exc.code == self.code, f"got {exc.code}: {exc}"
        return True
