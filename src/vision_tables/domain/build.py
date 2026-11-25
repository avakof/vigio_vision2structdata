"""assemble(): readings -> canonical Document (entities, observations, coverage, issues)."""
from __future__ import annotations

import hashlib
from collections import Counter

from ..config import ProcessingError
from ..contracts import (Coverage, Document, Entity, Evidence, Ingested, Issue, Observation, PageReading, Region,
                         Run)
from ..vocabulary import CALCULATED_ONLY, FAMILIES, FIELDS
from . import catalog
from .normalize import date_order_hint, normalize, unresolved
from .rules import recompute
from .schema import FIELD_TYPES, JOIN_ROOT, PARENTS, REQUIRED_PARENT, ROOT_KIND

CLAIMS = {"dietary_claim", "allergen_claim"}


def _ident(prefix: str, local: str, limit: int = 140) -> str:
    text = f"{prefix}{local}"
    return text if len(text) <= limit else f"{prefix}h{hashlib.sha256(local.encode()).hexdigest()[:20]}"


def _dedupe(items, limit=16):
    return list(dict.fromkeys(i for i in items if i))[:limit]


def _resolve_type(readings: list[PageReading], issues: list[Issue]) -> str:
    weight: Counter = Counter()
    for r in readings:
        if r.doc_type != "unknown":
            weight[r.doc_type] += max(1, sum(len(rec.fields) for rec in r.records))
    if not weight:
        return "unknown"
    ranked = weight.most_common()
    if len(ranked) > 1:
        top, second = ranked[0], ranked[1]
        if top[1] == second[1]:
            issues.append(catalog.make("doc_type_conflict", "pages disagree on the document type: " +
                                       ", ".join(f"{t} (pages {[r.page for r in readings if r.doc_type == t]})" for t, _ in ranked)))
            return "unknown"
        issues.append(catalog.make("doc_type_conflict", "pages disagree on the document type; majority " + top[0] +
                                   ", the other pages were not assembled"))
    return ranked[0][0]


def assemble(ingested: Ingested, readings: list[PageReading], scope: list[int], run: Run, document_id: str) -> Document:
    """Assemble one document. Inputs outside ``scope`` are ignored; nothing observed is altered or guessed."""
    if not scope or scope != sorted(set(scope)) or scope[0] < 1 or scope[-1] > ingested.source.page_count:
        raise ProcessingError("invalid_scope", "Document scope must be sorted, unique, one-based pages of the source.")
    in_scope = set(scope)
    pages = [p for p in ingested.pages if p.number in in_scope]
    page_numbers = {p.number for p in pages}
    mine = sorted((r for r in readings if r.page in in_scope), key=lambda r: r.page)
    if len({r.page for r in mine}) != len(mine):
        raise ProcessingError("duplicate_page_reading", "Two readings were supplied for one page.")
    issues: list[Issue] = [i for i in ingested.issues if i.page is None or i.page in in_scope]

    # ---- coverage ------------------------------------------------------------------------------------
    skipped = sorted((set(ingested.skipped_pages) & in_scope) | (in_scope - page_numbers))
    for n in sorted(in_scope - page_numbers - set(ingested.skipped_pages)):
        issues.append(catalog.make("page_not_ingested", f"page {n} has no ingested image", page=n))
    read = {r.page: r for r in mine if r.page in page_numbers and r.page not in ingested.skipped_pages}
    incomplete = set()
    for n in sorted(page_numbers - set(ingested.skipped_pages)):
        reading = read.get(n)
        if reading is None:
            incomplete.add(n)
            issues.append(catalog.make("page_not_read", f"page {n} was ingested but has no reading", page=n))
        elif reading.incomplete:
            incomplete.add(n)
            if not reading.issues:
                issues.append(catalog.make("page_incomplete", f"page {n} reading is incomplete", page=n))
    capture = any(r.suspected_cutoff for r in read.values())
    for n in sorted(r.page for r in read.values() if r.suspected_cutoff):
        issues.append(catalog.make("suspected_cutoff", f"page {n} may be cut off at the edge of the capture", page=n))
    for r in read.values():
        issues.extend(i for i in r.issues if i.page is None or i.page == r.page)

    # ---- regions ---------------------------------------------------------------------------------------
    page_map = {p.number: p for p in pages}
    regions: dict[str, Region] = {}
    for region in [*(r for r in ingested.regions if r.page in in_scope), *(g for r in read.values() for g in r.regions)]:
        page = page_map.get(region.page)
        box = region.box.xyxy if region.box else None
        if page is None or region.page not in in_scope or (box and (box[2] > page.width + 0.001 or box[3] > page.height + 0.001)):
            issues.append(catalog.make("invalid_region", f"region {region.id} is outside its page", page=region.page if page else None))
        elif region.id in regions:
            if regions[region.id] != region:
                issues.append(catalog.make("region_conflict", f"region {region.id} was supplied twice with different content", page=region.page))
        else:
            regions[region.id] = region

    # ---- document type -----------------------------------------------------------------------------------
    doc_type = _resolve_type(list(read.values()), issues)

    # ---- entities ----------------------------------------------------------------------------------------
    degraded: set[int] = set()  # pages whose content was dropped or could not be classified: not complete
    entities: list[Entity] = []
    used_ids: set[str] = set()
    sources: list[tuple[Entity, object]] = []
    for reading in mine:
        if reading.page not in read:
            continue
        if reading.doc_type == "unknown" and reading.records:
            degraded.add(reading.page)
            issues.append(catalog.make("unclassified_page", f"page {reading.page} was not classified as {doc_type}", page=reading.page))
        elif reading.doc_type != doc_type and reading.records:
            degraded.add(reading.page)
            issues.append(catalog.make("page_doc_type_mismatch", f"page {reading.page} reads as {reading.doc_type}, not {doc_type}; "
                                       f"{len(reading.records)} records were not assembled", page=reading.page))
            continue
        local_ids: dict[str, str] = {}
        page_entities: list[tuple[Entity, object]] = []
        for rec in reading.records:
            if rec.kind not in FAMILIES[reading.doc_type] or rec.kind not in FIELDS:
                degraded.add(reading.page)
                issues.append(catalog.make("unsupported_kind", f"record kind {rec.kind!r} is not in the {reading.doc_type} vocabulary",
                                           page=reading.page))
                continue
            eid = _ident(f"p{reading.page}.", rec.local_id)
            if rec.local_id in local_ids or eid in used_ids:
                issues.append(catalog.make("duplicate_local_id", f"local id {rec.local_id!r} repeated on page {reading.page}", page=reading.page))
                eid = _ident(f"p{reading.page}.", f"{rec.local_id}.dup{len(entities)}")
            else:
                local_ids[rec.local_id] = eid
            entity = Entity(id=eid, kind=rec.kind, parent_id=None, page=reading.page, ordinal=len(entities))
            used_ids.add(eid)
            entities.append(entity)
            page_entities.append((entity, rec))
        sources.extend(page_entities)
        kinds = {e.id: e.kind for e, _ in page_entities}
        for entity, rec in page_entities:
            if rec.parent_id is None:
                continue
            parent = local_ids.get(rec.parent_id)
            if parent is None or kinds[parent] not in PARENTS[entity.kind]:
                why = "unknown parent" if parent is None else f"{kinds[parent]} cannot contain {entity.kind}"
                issues.append(catalog.make("invalid_parent", f"{entity.kind} {rec.local_id}: {why}; link ignored", page=entity.page))
            else:
                entity.parent_id = parent
        index = {e.id: e for e, _ in page_entities}
        for entity, _ in page_entities:  # break parent cycles
            seen, cursor = {entity.id}, entity
            while cursor.parent_id:
                cursor = index[cursor.parent_id]
                if cursor.id in seen:
                    issues.append(catalog.make("invalid_parent", f"{entity.kind} parent chain loops; link ignored", page=entity.page))
                    entity.parent_id = None
                    break
                seen.add(cursor.id)
    _join_roots(entities, doc_type, issues)

    incomplete |= degraded  # read, but part of the page is missing from the document

    # ---- observations ------------------------------------------------------------------------------------
    channel = "direct" if run.pipeline == "direct" else "parser"
    order_hint = _document_date_order(sources)
    observations: list[Observation] = []
    deferred: list[tuple[Entity, str, int]] = []
    kind_of = {e.id: e.kind for e in entities}
    for entity, rec in sources:
        for name, fr in rec.fields.items():
            if name not in FIELDS[entity.kind] or name in CALCULATED_ONLY:
                incomplete.add(entity.page)
                issues.append(catalog.make("unsupported_field", f"{entity.kind}.{name} is not an observable field", page=entity.page))
                continue
            region_id = fr.region_id
            region = regions.get(region_id) if region_id else None
            if region_id and (region is None or region.page != entity.page):
                issues.append(catalog.make("invalid_evidence", f"{entity.kind}.{name} cites region {region_id}, which is not a region of page {entity.page}",
                                           page=entity.page, observations=[f"{entity.id}.{name}"]))
                region_id, region = None, None
            evidence = read[entity.page].field_evidence.get(rec.local_id, {}).get(name, [])
            if not evidence:
                evidence = [Evidence(page=entity.page, region_id=region_id, quote=fr.evidence, channel=channel)]
            observation = _observe(entity, name, fr, region, evidence, order_hint)
            observations.append(observation)
            if FIELD_TYPES[entity.kind][name] == "ref" and observation.state == "readable":
                deferred.append((entity, name, len(observations) - 1))
    ids_by_page = {(e.page, rec.local_id): e.id for e, rec in sources}
    for entity, _, index in deferred:
        observation = observations[index]
        target = ids_by_page.get((entity.page, observation.raw.strip()))
        if target is not None and kind_of[target] == "menu_item":
            observations[index] = observation.model_copy(update={"value": target})
        else:
            observations[index] = observation.model_copy(update={
                "value": None, "state": "ambiguous", "reason": "item_ref does not name a menu item on this page"})

    # ---- derive, check, finalize --------------------------------------------------------------------------
    issues = catalog.bind_field_issues(issues, observations, entities)
    return finalize(doc_type, document_id, scope, ingested.source, ingested.source_skipped_pages, pages, list(regions.values()), run, entities, observations,
                    issues, skipped, sorted(incomplete), sorted(read), capture)


def finalize(doc_type, document_id, scope, source, source_skipped, pages, regions, run, entities, observations, carried, skipped,
             incomplete, processed, capture, *, version=1, parent_version=None, approval="needs_review", corrections=()):
    final, derived = recompute(doc_type, entities, observations, regions, set(incomplete))
    if not entities:
        carried = [*carried, catalog.make("empty_document", "no record was assembled from the processed pages")]
    valid_obs = {o.id for o in final}
    human = {o.id for o in final if o.origin == "human_supplied"}
    all_issues, seen = [], set()
    for issue in [*(catalog.classify_carried(i, human) for i in carried), *derived]:
        issue = issue.model_copy(update={"observation_ids": [i for i in issue.observation_ids if i in valid_obs]})
        key = (issue.code, issue.message, issue.page, issue.severity, tuple(issue.observation_ids))
        if key not in seen:
            seen.add(key)
            all_issues.append(issue)
    coverage = Coverage(processed_pages=processed, skipped_pages=skipped, source_skipped_pages=sorted(source_skipped),
                        incomplete_pages=incomplete, suspected_capture=capture,
                        original_capture="suspected_incomplete" if capture else "not_established")
    unfinished = bool(skipped or incomplete or coverage.source_skipped_pages or not entities)
    unresolved_review = capture or any(i.severity == "review" for i in all_issues)
    validation = "incomplete" if unfinished else "issues" if unresolved_review else "valid"
    return Document(document_id=document_id, version=version, parent_version=parent_version, doc_type=doc_type, scope=list(scope),
                    source=source, pages=pages, regions=regions, run=run, coverage=coverage, entities=entities,
                    observations=final, issues=all_issues, validation=validation, approval=approval, corrections=list(corrections))


def _document_date_order(sources) -> str | None:
    """A day/month order forced by an unambiguous numeric date, when all such dates agree."""
    orders = {date_order_hint(fr.raw) for entity, rec in sources for name, fr in rec.fields.items()
              if FIELD_TYPES[entity.kind].get(name) == "date" and fr.raw and fr.state == "readable"}
    orders.discard(None)
    return orders.pop() if len(orders) == 1 else None


def _join_roots(entities: list[Entity], doc_type: str, issues: list[Issue]) -> None:
    if doc_type == "unknown":
        return
    root_kind = ROOT_KIND[doc_type]
    roots = [e for e in entities if e.kind == root_kind]
    joined: dict[tuple[int, str], int] = {}
    for entity in entities:
        if entity.parent_id is not None or entity.kind not in JOIN_ROOT or root_kind not in PARENTS[entity.kind]:
            continue
        same_page = [r for r in roots if r.page == entity.page]
        target, mode = None, ""
        if len(same_page) == 1:
            target, mode = same_page[0], "same page"
        elif not same_page and len(roots) == 1:
            target, mode = roots[0], f"continuation of page {roots[0].page}"
        if target is not None:
            entity.parent_id = target.id
            joined[(entity.page, mode)] = joined.get((entity.page, mode), 0) + 1
    for (page, mode), count in sorted(joined.items()):
        issues.append(catalog.make("parent_joined", f"{count} records without a parent were joined to the {root_kind} ({mode})", page=page))
    for entity in entities:
        if entity.parent_id is None and entity.kind in REQUIRED_PARENT:
            issues.append(catalog.make("orphan_record", f"{entity.kind} on page {entity.page} has no parent", page=entity.page))


def _observe(entity: Entity, name: str, fr, region: Region | None, evidence: list[Evidence], order_hint) -> Observation:
    lexical = FIELD_TYPES[entity.kind][name]
    oid = f"{entity.id}.{name}"
    language = fr.language or (region.language if region else None)
    kw = dict(id=oid, entity_id=entity.id, field=name, origin="observed", language=language, evidence=evidence)
    alternatives = _dedupe(fr.alternatives)
    if fr.state == "readable":
        if lexical == "ref":
            return Observation(raw=fr.raw, value=fr.raw.strip(), state="readable", alternatives=alternatives, **kw)
        parsed = normalize(lexical, fr.raw, order=order_hint)
        if name in CLAIMS and not (fr.evidence.strip() or fr.region_id):
            parsed = unresolved("claim has no supporting quote; claims are stored only when explicitly stated", [])
        if parsed.state == "readable":
            return Observation(raw=fr.raw, value=parsed.value, state="readable", alternatives=alternatives,
                               reason=parsed.reason or fr.reason, **kw)
        return Observation(raw=fr.raw, value=None, state="ambiguous", alternatives=_dedupe(parsed.alternatives + alternatives),
                           reason=parsed.reason, **kw)
    if fr.state == "ambiguous":
        return Observation(raw=fr.raw, value=None, state="ambiguous", alternatives=alternatives,
                           reason=fr.reason or (None if alternatives else "model reported ambiguity"), **kw)
    if fr.state == "unreadable":
        return Observation(raw=fr.raw, value=None, state="unreadable", alternatives=alternatives,
                           reason=fr.reason or ("candidates: " + "; ".join(alternatives) if alternatives else "unreadable"), **kw)
    return Observation(raw=None, value=None, state="not_present", alternatives=[], reason=fr.reason, **kw)
