"""Revalidation, human corrections as new immutable versions, and the approval gate."""
from __future__ import annotations

import re
from datetime import date
from fractions import Fraction
from math import gcd

from ..config import ProcessingError
from ..contracts import Correction, CorrectionSet, Document, Evidence, Issue, Observation
from . import catalog
from .build import finalize
from .normalize import clean_text
from .schema import ENUMS, FIELD_TYPES
from .units import DIMENSION, ISO_CODES

_DECIMAL = re.compile(r"-?(?:0|[1-9]\d{0,23})(?:\.\d{1,12})?")
_FRACTION = re.compile(r"-?\d{1,24}/\d{1,24}")


def _non_terminating_fraction(value: str) -> bool:
    """Reduced 'n/d' whose decimal expansion does not terminate (the only form format_number emits)."""
    if not _FRACTION.fullmatch(value):
        return False
    n, d = (abs(int(part)) for part in value.split("/"))
    if d < 2 or gcd(n, d) != 1:
        return False
    for p in (2, 5):
        while d % p == 0:
            d //= p
    return d != 1


def _carried(document: Document, human: set[str]) -> list[Issue]:
    keep = []
    for issue in document.issues:
        if issue.code in catalog.DERIVED:
            continue
        if issue.code in catalog.OBSERVATION_BOUND and issue.observation_ids and set(issue.observation_ids) <= human:
            continue
        keep.append(issue)
    return keep


def _build(document: Document, observations: list[Observation], *, version: int, parent_version: int | None,
           approval: str, corrections) -> Document:
    human = {o.id for o in observations if o.origin == "human_supplied"}
    return finalize(document.doc_type, document.document_id, document.scope, document.source,
                    document.coverage.source_skipped_pages, document.pages, document.regions, document.run,
                    document.entities, observations, _carried(document, human), document.coverage.skipped_pages,
                    document.coverage.incomplete_pages, document.coverage.processed_pages,
                    document.coverage.suspected_capture, version=version, parent_version=parent_version,
                    approval=approval, corrections=corrections)


def revalidate(document: Document) -> Document:
    """Recalculate derived observations and domain issues of this exact version. Pure and idempotent.

    Carried (ingestion/adapter/assembly) issues are kept. A human approval is kept only while no review
    issue, incompleteness or suspected capture exists; otherwise the result drops back to needs_review.
    """
    kept = [o for o in document.observations]
    candidate = _build(document, kept, version=document.version, parent_version=document.parent_version,
                       approval="needs_review", corrections=document.corrections)
    if document.approval == "human_approved" and review_gate(candidate)["approvable"]:
        return Document.model_validate({**candidate.model_dump(mode="python"), "approval": "human_approved"})
    return candidate


def review_gate(document: Document) -> dict:
    """What stands between this version and human approval, with the resolution for each blocker."""
    gates = catalog.review_gates()[document.doc_type]
    blockers = [{"code": i.code, "message": i.message, "page": i.page, "observation_ids": i.observation_ids,
                 "resolution": gates.get(i.code, "Resolve the adapter issue or reprocess.")}
                for i in document.issues if i.severity == "review"]
    reasons = []
    if document.doc_type == "unknown":
        reasons.append("document type is unknown")
    if not document.observations:
        reasons.append("no observations")
    if document.validation == "incomplete":
        reasons.append("coverage or processing is incomplete; reprocess")
    if document.coverage.suspected_capture:
        reasons.append("suspected incomplete capture; reprocess from a complete capture")
    return {"approvable": not blockers and not reasons, "blockers": blockers, "reasons": reasons,
            "informational": [{"code": i.code, "message": i.message} for i in document.issues if i.severity == "info"]}


def _canonical_error(lexical: str, value, entities: dict) -> str | None:
    if lexical == "flag":
        return None if isinstance(value, bool) else "expected true or false"
    if not isinstance(value, str):
        return "expected a string"
    if lexical == "text":
        return None if value and clean_text(value) == value else "expected non-empty single-spaced text"
    if lexical in ("money", "percent") or lexical.startswith("temp_"):
        if not _DECIMAL.fullmatch(value):
            return "expected a canonical decimal string such as '1234.50'"
        number = Fraction(value)
        if lexical == "percent" and not 0 <= number <= 100:
            return "percent must be within 0..100"
        return None
    if lexical in ("number", "quantity", "duration"):
        if not (_DECIMAL.fullmatch(value) or _non_terminating_fraction(value)):
            return "expected a decimal string, or 'n/d' for a non-terminating fraction"
        return "duration cannot be negative" if lexical == "duration" and value.startswith("-") else None
    if lexical == "count":
        return None if re.fullmatch(r"[1-9]\d{0,5}", value) else "expected a positive integer string"
    if lexical == "date":
        try:
            ok = re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) and date.fromisoformat(value).isoformat() == value
        except ValueError:
            ok = False
        return None if ok else "expected an ISO date YYYY-MM-DD"
    if lexical == "currency":
        return None if value in ISO_CODES else "expected an ISO 4217 code"
    if lexical in ("unit", "price_basis"):
        return None if value in DIMENSION else "expected a canonical unit code"
    if lexical.startswith("enum:"):
        return None if value in ENUMS[lexical[5:]] else "expected one of " + ", ".join(ENUMS[lexical[5:]])
    if lexical == "ref":
        return None if value in entities and entities[value].kind == "menu_item" else "expected the id of a menu_item entity"
    return f"no check for {lexical}"


def _human(old: Observation, entity, change: Correction, reviewer: str) -> Observation:
    was = f"model read {old.raw[:400]!r} as {old.value!r} ({old.state})" if old.origin == "observed" else f"previous value {old.value!r} ({old.state})"
    reason = f"{reviewer}: {change.reason} [{was}]"[:4000]
    return Observation(id=old.id, entity_id=old.entity_id, field=old.field, raw=change.raw, value=change.value,
                       state=change.state, origin="human_supplied", language=old.language,
                       evidence=[*old.evidence, Evidence(page=entity.page, quote=change.raw or "", channel="human")],
                       alternatives=[], reason=reason)


def apply_corrections(document: Document, changes: CorrectionSet) -> Document:
    """Apply typed human changes atomically as version N+1; the input document is never modified.

    Original model observations stay in the parent version (and in each replacement's reason and
    evidence). Calculated observations cannot be edited; they are recomputed. An explicit approval is
    accepted only for the exact resulting version and only when the review gate is clear.
    """
    if changes.document_id != document.document_id:
        raise ProcessingError("document_mismatch", "The corrections belong to another document.")
    if changes.base_version != document.version:
        raise ProcessingError("stale_base", f"Corrections target version {changes.base_version}; this is version {document.version}.")
    index = {o.id: o for o in document.observations}
    entities = {e.id: e for e in document.entities}
    replacements: dict[str, Observation] = {}
    problems = []
    for change in changes.changes:
        old = index.get(change.observation_id)
        if old is None:
            problems.append(f"{change.observation_id}: unknown observation")
            continue
        if old.origin == "calculated":
            problems.append(f"{change.observation_id}: calculated observations are recomputed, never edited")
            continue
        entity = entities[old.entity_id]
        error = _canonical_error(FIELD_TYPES[entity.kind][old.field], change.value, entities) if change.state == "readable" else None
        if error:
            problems.append(f"{change.observation_id}: {error}")
            continue
        replacements[old.id] = _human(old, entity, change, changes.reviewer)
    if problems:
        raise ProcessingError("invalid_correction", "No change was applied. " + "; ".join(problems[:10]))
    observations = [replacements.get(o.id, o) for o in document.observations]
    history = [*document.corrections, changes]
    kw = dict(version=document.version + 1, parent_version=document.version, corrections=history)
    candidate = _build(document, observations, approval="needs_review", **kw)
    if not changes.approve:
        return candidate
    gate = review_gate(candidate)
    if not gate["approvable"]:
        blockers = [f"{b['code']} ({b['message']})" for b in gate["blockers"][:8]] + gate["reasons"]
        raise ProcessingError("approval_blocked", "This version cannot be approved: " + "; ".join(blockers))
    return Document.model_validate({**candidate.model_dump(mode="python"), "approval": "human_approved"})
