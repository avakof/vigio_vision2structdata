"""Score independent labels through an adapter to canonical extraction results."""

from __future__ import annotations

import hashlib
import json
import re
import resource
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Callable
from uuid import uuid4

if TYPE_CHECKING:
    from .config import Settings
    from .contracts import Document


Scalar = str | bool | None


@dataclass(frozen=True)
class Reading:
    value: Scalar
    state: str = "readable"
    alternatives: tuple[str, ...] = ()
    pages: tuple[int, ...] = ()
    raw: str | None = None
    origin: str | None = None
    inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntityResult:
    id: str
    kind: str
    fields: dict[str, Reading]
    parent_id: str | None = None


@dataclass(frozen=True)
class DocumentResult:
    document_type: str
    scope: tuple[int, ...]
    entities: tuple[EntityResult, ...] = ()
    approval: str | None = None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceResult:
    status: str
    documents: tuple[DocumentResult, ...] = ()
    page_count: int | None = None
    processed_pages: tuple[int, ...] = ()
    skipped_pages: tuple[int, ...] = ()
    incomplete_pages: tuple[int, ...] = ()
    suspected_capture: bool = False
    issues: tuple[str, ...] = ()
    detail: str | None = None
    runtime: dict = field(default_factory=dict)


Extractor = Callable[[Path, Path, dict], SourceResult]

_FIXTURE_ID = re.compile(r"[a-z0-9][a-z0-9_-]*")
_ORIGINS = {"synthetic", "real"}
_SPLITS = {"development", "held_out"}
_STATES = {"readable", "ambiguous", "unreadable", "not_present"}
_FIELD_METRICS = {
    "critical_numeric", "menu_price", "recipe_quantity_unit_yield", "recipe_time_temperature",
}
_RECIPE_IDENTITY_FIELDS = {
    "recipe": ("title",), "ingredient": ("name",), "step": ("instruction",),
    "subrecipe": ("name", "title"),
}
_YIELD_FIELDS = ("yield_quantity", "yield_unit", "yield_type")
_CASE_SETTINGS = {
    "doc_type", "page_groups", "max_bytes", "max_pages", "max_pixels", "render_pixels",
    "max_crops", "max_tokens", "retries",
}


def _check_keys(record, required, optional, context):
    if not isinstance(record, dict):
        raise ValueError(f"Expected an object in {context}")
    missing = set(required) - record.keys()
    unknown = record.keys() - set(required) - set(optional)
    if missing or unknown:
        raise ValueError(f"{context}: missing={sorted(missing)}, unknown={sorted(unknown)}")


def _verified_file(root: Path, relative: str, sha256: str) -> Path:
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root):
        raise ValueError(f"Fixture path must be relative to the manifest: {relative}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != sha256:
        raise ValueError(f"Fixture SHA-256 mismatch for {relative}: got {digest}")
    return path


def _validate_labels(labels: dict, fixture_id: str) -> None:
    _check_keys(labels, ("status", "eligible"), ("page_count", "processed_pages",
                "skipped_pages", "incomplete_pages", "suspected_capture", "documents",
                "issues"), fixture_id)
    if type(labels["eligible"]) is not bool:
        raise ValueError(f"{fixture_id}: eligible must be a boolean")
    scopes = set()
    for document in labels.get("documents", []):
        _check_keys(document, ("type", "scope", "entities"), ("issues", "exhaustive"), fixture_id)
        scope = tuple(document["scope"])
        if scope in scopes or not scope or tuple(sorted(set(scope))) != scope or scope[0] < 1:
            raise ValueError(f"{fixture_id}: invalid or repeated document page scope {scope}")
        scopes.add(scope)
        entities = document["entities"]
        ids = {entity["id"] for entity in entities}
        alignment = {}
        if len(ids) != len(entities):
            raise ValueError(f"{fixture_id}: repeated entity IDs in {scope}")
        for entity in entities:
            _check_keys(entity, ("id", "kind", "fields"), ("parent", "row", "anchor"), fixture_id)
            parent = entity.get("parent")
            if parent is not None and (parent == entity["id"] or parent not in ids):
                raise ValueError(f"{fixture_id}: invalid parent {parent}")
            anchored = "anchor" in entity
            if alignment.setdefault(entity["kind"], anchored) != anchored:
                raise ValueError(f"{fixture_id}: use one matching rule per entity kind")
            if anchored:
                anchor_label = entity["fields"].get(entity["anchor"])
                if not isinstance(anchor_label, str) or entity["kind"] in document.get("exhaustive", []):
                    raise ValueError(f"{fixture_id}: anchor requires a text field in a sparse kind")
            for label in entity["fields"].values():
                if isinstance(label, dict):
                    _check_keys(label, ("value",), ("state", "decimal", "metric", "alternatives",
                                "page", "raw", "origin", "inputs", "allow_missing"), fixture_id)
                    state = label.get("state", "readable")
                    if state not in _STATES or ((state == "readable") == (label["value"] is None)):
                        raise ValueError(f"{fixture_id}: invalid reading value or state")
                    if label.get("page", scope[0]) not in scope:
                        raise ValueError(f"{fixture_id}: labeled evidence page is outside {scope}")
                    if "metric" in label and label["metric"] not in _FIELD_METRICS:
                        raise ValueError(f"{fixture_id}: unknown field metric {label['metric']}")
                    if label.get("allow_missing") and state != "not_present":
                        raise ValueError(f"{fixture_id}: allow_missing requires not_present")
                elif type(label) not in (str, bool):
                    raise ValueError(f"{fixture_id}: specify a state for null or use a scalar label")


def load_manifest(manifest: Path):
    """Verify every source and label before running any extraction."""
    root = manifest.resolve().parent
    data = json.loads(manifest.read_text(encoding="utf-8"))
    _check_keys(data, ("version", "cases"), ("description",), str(manifest))
    if data["version"] != 1 or not data["cases"]:
        raise ValueError("Expected a nonempty version 1 evaluation manifest")
    fixture_ids = set()
    splits = {}
    cases = []
    for case in data["cases"]:
        _check_keys(case, ("id", "source", "sha256", "labels", "labels_sha256", "provenance",
                           "split", "family", "strata"), ("settings",), str(manifest))
        key = case["id"]
        if not _FIXTURE_ID.fullmatch(key) or key in fixture_ids:
            raise ValueError(f"Invalid or repeated fixture ID: {key}")
        fixture_ids.add(key)
        provenance = case["provenance"]
        _check_keys(provenance, ("origin", "source", "license", "labeler"),
                    ("notes",), key)
        if provenance["origin"] not in _ORIGINS or case["split"] not in _SPLITS:
            raise ValueError(f"{key}: invalid origin or split")
        if any(not provenance[name] for name in ("source", "license", "labeler")):
            raise ValueError(f"{key}: incomplete provenance")
        if (not case["strata"] or len(case["strata"]) != len(set(case["strata"]))
                or "all" in case["strata"]):
            raise ValueError(f"{key}: strata must be nonempty and unique")
        if set(case.get("settings", {})) - _CASE_SETTINGS:
            raise ValueError(f"{key}: unsupported configuration override")
        for family_key in (("family", case["family"]), ("source", case["sha256"])):
            if splits.setdefault(family_key, case["split"]) != case["split"]:
                raise ValueError(f"{key}: related fixtures span evaluation splits")
        source = _verified_file(root, case["source"], case["sha256"])
        label_path = _verified_file(root, case["labels"], case["labels_sha256"])
        labels = json.loads(label_path.read_text(encoding="utf-8"))
        _validate_labels(labels, key)
        cases.append((case, source, labels))
    return cases


def _equal(expected: Scalar, actual: Scalar, decimal: bool) -> bool:
    if not decimal:
        return type(expected) is type(actual) and expected == actual
    if not isinstance(expected, str) or not isinstance(actual, str):
        return False
    try:
        left, right = Decimal(expected), Decimal(actual)
    except InvalidOperation:
        return False
    return left.is_finite() and right.is_finite() and left == right


def _field_result(label: dict, reading: Reading | None) -> tuple[bool, bool, bool]:
    positive = label["value"] is not None
    covered = reading is not None and reading.state == "readable" and reading.value is not None
    if reading is None:
        return positive, covered, bool(label.get("allow_missing"))
    correct = (
        _equal(label["value"], reading.value, label.get("decimal", False))
        and label.get("state", "readable") == reading.state
        and ("page" not in label or label["page"] in reading.pages)
        and ("alternatives" not in label
             or set(label["alternatives"]) == set(reading.alternatives))
        and ("raw" not in label or label["raw"] == reading.raw)
        and ("origin" not in label or label["origin"] == reading.origin)
        and ("inputs" not in label or set(label["inputs"]) == set(reading.inputs))
    )
    return positive, covered, correct


def _check(counts: Counter, failures: list, metric: str, ok: bool, location: str) -> None:
    counts[f"{metric}_n"] += 1
    counts[f"{metric}_ok"] += int(ok)
    if not ok:
        failures.append({"metric": metric, "location": location})


def _verified_missing_recipe_yield(source: SourceResult, document: DocumentResult | None) -> bool:
    if (document is None or document.document_type != "recipe" or source.status != "completed"
            or source.suspected_capture or "recipe_yield_missing" not in document.issues):
        return False
    pages = set(document.scope)
    if not pages <= set(source.processed_pages) or pages.intersection(
        source.skipped_pages + source.incomplete_pages
    ):
        return False
    roots = [row for row in document.entities if row.kind == "recipe"]
    # The issue is document-level here. With multiple roots it cannot attest to a particular root.
    if len(roots) != 1:
        return False
    for name in _YIELD_FIELDS:
        reading = roots[0].fields.get(name)
        if reading is not None and (
            reading.state != "not_present" or reading.value is not None
            or reading.raw or reading.alternatives
        ):
            return False
    return any(
        reading.state == "readable" and isinstance(reading.value, str) and reading.value.strip()
        and pages.intersection(reading.pages)
        for row in document.entities
        for name in _RECIPE_IDENTITY_FIELDS.get(row.kind, ())
        if (reading := row.fields.get(name)) is not None
    )


def _score_document(expected, actual, source, counts, failures):
    location = f"pages {expected['scope']}"
    _check(counts, failures, "document_type", actual is not None and
           actual.document_type == expected["type"], location)
    for issue in expected.get("issues", []):
        _check(counts, failures, "required_issue", actual is not None and issue in actual.issues,
               f"{location}/{issue}")

    by_kind = defaultdict(list)
    if actual:
        for entity in actual.entities:
            by_kind[entity.kind].append(entity)
    next_index = Counter()
    matches = {}
    matched_ids = set()
    verified_missing_yield = _verified_missing_recipe_yield(source, actual)
    for entity in expected["entities"]:
        kind = entity["kind"]
        index = next_index[kind]
        candidates = by_kind[kind]
        anchor = entity.get("anchor")
        if anchor:
            expected_value = entity["fields"][anchor]
            eligible = [row for row in candidates if row.id not in matched_ids and
                        row.fields.get(anchor) is not None and
                        row.fields[anchor].state == "readable" and
                        row.fields[anchor].value == expected_value]
            match = eligible[0] if len(eligible) == 1 else None
        else:
            match = candidates[index] if index < len(candidates) else None
        if match:
            matched_ids.add(match.id)
        matches[entity["id"]] = match
        next_index[kind] += 1
    for entity in expected["entities"]:
        identifier = entity["id"]
        actual_entity = matches[identifier]
        row_location = f"{location}/{identifier}"
        row_correct = actual_entity is not None
        if "parent" in entity:
            parent_id = entity["parent"]
            parent = matches.get(parent_id)
            related = actual_entity is not None and (
                actual_entity.parent_id is None if parent_id is None
                else parent is not None and actual_entity.parent_id == parent.id
            )
            _check(counts, failures, "relationship", related, row_location)
            row_correct &= related
        for name, label in entity["fields"].items():
            if not isinstance(label, dict):
                label = {"value": label}
            reading = actual_entity.fields.get(name) if actual_entity else None
            missing_yield_label = (
                expected["type"] == entity["kind"] == "recipe" and name == "yield_quantity"
                and label.get("state") == "not_present"
            )
            scoring_reading = reading
            if missing_yield_label and verified_missing_yield and reading is None:
                # The corroboration establishes absence for the fully processed scope.
                scoring_reading = Reading(None, "not_present", pages=actual.scope)
            positive, covered, correct = _field_result(label, scoring_reading)
            location_field = f"{row_location}/{name}"
            correct &= actual_entity is not None and (not missing_yield_label or verified_missing_yield)
            _check(counts, failures, "field_exact", correct, location_field)
            if positive:
                counts["answerable_fields"] += 1
                counts["covered_answerable_fields"] += int(covered)
                counts["correct_answered_fields"] += int(covered and correct)
            else:
                counts["unresolved_fields"] += 1
                counts["guessed_unresolved_fields"] += int(
                    reading is not None and reading.value is not None
                )
            if label.get("metric"):
                _check(counts, failures, label["metric"], correct, location_field)
            row_correct &= correct
        if entity.get("row", False):
            _check(counts, failures, "row_alignment", row_correct, row_location)

    for kind in expected.get("exhaustive", []):
        expected_count, actual_count = next_index[kind], len(by_kind[kind])
        _check(counts, failures, "entity_count", expected_count == actual_count,
               f"{location}/{kind}")
        counts["unexpected_entities"] += max(0, actual_count - expected_count)


def score_case(labels: dict, actual: SourceResult) -> tuple[Counter, list]:
    """Compare canonical results with independently maintained case labels."""
    counts = Counter(attempts=1, documents=len(actual.documents))
    failures = []
    _check(counts, failures, "status", actual.status == labels["status"], "source")
    for key in ("page_count", "processed_pages", "skipped_pages", "incomplete_pages",
                "suspected_capture"):
        if key in labels:
            expected = labels[key]
            if isinstance(expected, list):
                expected = tuple(expected)
            _check(counts, failures, "page_coverage", getattr(actual, key) == expected, key)
    for issue in labels.get("issues", []):
        _check(counts, failures, "required_issue", issue in actual.issues, f"source/{issue}")
    if "documents" in labels:
        document_map = {document.scope: document for document in actual.documents}
        expected_scopes = {tuple(document["scope"]) for document in labels["documents"]}
        _check(counts, failures, "document_scope",
               set(document_map) == expected_scopes and len(document_map) == len(actual.documents),
               "source")
        for document in labels["documents"]:
            _score_document(document, document_map.get(tuple(document["scope"])), actual, counts, failures)

    review_count = sum(doc.approval == "review" for doc in actual.documents)
    auto_count = sum(doc.approval == "auto_accepted" for doc in actual.documents)
    counts["documents_with_known_approval"] = sum(
        doc.approval is not None for doc in actual.documents
    )
    counts["review_documents"] = review_count
    counts["auto_accepted_documents"] = auto_count
    counts["auto_accepted_cases"] = int(auto_count > 0)
    counts["ineligible_cases"] = int(not labels["eligible"])
    counts["auto_accepted_ineligible_cases"] = int(auto_count > 0 and not labels["eligible"])
    counts["false_accepted_cases"] = int(auto_count > 0 and (bool(failures) or not labels["eligible"]))
    counts["failed_cases"] = int(bool(failures))
    return counts, failures


def _fraction(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "rate": numerator / denominator if denominator else None}


def _metrics(counts):
    metrics = {key[:-2]: _fraction(counts[f"{key[:-2]}_ok"], value)
               for key, value in counts.items() if key.endswith("_n")}
    for name, numerator, denominator in (
        ("case_pass", "passed_cases", "attempts"),
        ("answerable_field_coverage", "covered_answerable_fields", "answerable_fields"),
        ("accuracy_when_answered", "correct_answered_fields", "covered_answerable_fields"),
        ("unresolved_guess_rate", "guessed_unresolved_fields", "unresolved_fields"),
        ("approval_status_coverage", "documents_with_known_approval", "documents"),
        ("review_rate", "review_documents", "documents_with_known_approval"),
        ("automatic_acceptance_rate", "auto_accepted_documents", "documents_with_known_approval"),
        ("false_acceptance_rate", "false_accepted_cases", "auto_accepted_cases"),
        ("unsafe_acceptance_on_ineligible_cases", "auto_accepted_ineligible_cases",
         "ineligible_cases"),
    ):
        metrics[name] = _fraction(counts[numerator], counts[denominator])
    metrics["answerable_field_abstention"] = _fraction(
        counts["answerable_fields"] - counts["covered_answerable_fields"], counts["answerable_fields"]
    )
    return metrics


def _runtime_summary(samples):
    phases = {}
    for phase in sorted({sample["phase"] for sample in samples}):
        group = [sample for sample in samples if sample["phase"] == phase]
        elapsed = [sample["seconds"] for sample in group]
        pages = sum(sample["processed_page_count"] for sample in group)
        page_samples = [sample for sample in group if sample["processed_page_count"]]
        covered_seconds = sum(sample["seconds"] for sample in page_samples)
        phases[phase] = {
            "samples": len(group), "noncompleted_runs": sum(sample["failed"] for sample in group),
            "seconds": {"min": min(elapsed), "mean": sum(elapsed) / len(elapsed), "max": max(elapsed)},
            "processed_pages": pages,
            "samples_with_processed_pages": len(page_samples),
            "end_to_end_seconds_per_processed_page": covered_seconds / pages if pages else None,
            "process_high_water_rss_max_bytes": max(sample["process_high_water_rss_bytes"]
                                                    for sample in group),
        }
    return phases


def evaluate_manifest(
    manifest: Path, output: Path, extract: Extractor, *, configuration: dict | None = None,
    splits: tuple[str, ...] = ("development", "held_out"), runtime_phase: str = "unspecified",
) -> dict:
    """Run a supplied local extractor, write evaluation.json and return the report."""
    if not splits or set(splits) - _SPLITS:
        raise ValueError(f"Unknown evaluation splits: {splits}")
    cases = [case for case in load_manifest(manifest) if case[0]["split"] in splits]
    if not cases:
        raise ValueError("No fixtures in requested splits")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    run_directory = Path(tempfile.mkdtemp(prefix="cases-", dir=output))
    results = []
    aggregates = defaultdict(Counter)
    runtime_samples = defaultdict(list)
    for case, source, labels in cases:
        path = run_directory / case["id"]
        path.mkdir()
        started = perf_counter()
        try:
            actual = extract(source, path, case.get("settings", {}))
        except Exception as exc:
            actual = SourceResult("error", detail=f"{type(exc).__name__}: {exc}")
        elapsed = perf_counter() - started
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        sample = {
            "fixture": case["id"], "phase": runtime_phase, "seconds": elapsed,
            "processed_page_count": len(actual.processed_pages),
            "failed": actual.status != "completed",
            "process_high_water_rss_bytes": rss if sys.platform == "darwin" else rss * 1024,
            "backend": actual.runtime,
        }
        counts, failures = score_case(labels, actual)
        counts["passed_cases"] = int(not failures)
        key = (case["provenance"]["origin"], case["split"])
        for stratum in ("all", *case["strata"]):
            aggregates[(*key, stratum)].update(counts)
        runtime_samples[key].append(sample)
        results.append({
            "fixture": case["id"], "origin": key[0], "split": key[1], "strata": case["strata"],
            "sha256": case["sha256"], "labels_sha256": case["labels_sha256"],
            "settings": case.get("settings", {}), "status": actual.status, "detail": actual.detail,
            "processed_pages": actual.processed_pages, "skipped_pages": actual.skipped_pages,
            "incomplete_pages": actual.incomplete_pages, "suspected_capture": actual.suspected_capture,
            "page_count": actual.page_count, "issues": actual.issues,
            "counts": dict(counts), "failures": failures,
            "runtime_sample": sample, "artifacts": str(path.relative_to(output)),
        })

    report = {
        "version": 1,
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "configuration": configuration if configuration is not None else {},
        "case_results": results,
        "metrics": [
            {"origin": key[0], "split": key[1], "stratum": key[2],
             "counts": dict(counts), "rates": _metrics(counts)}
            for key, counts in sorted(aggregates.items())
        ],
        "runtime": [
            {"origin": key[0], "split": key[1], "by_phase": _runtime_summary(samples)}
            for key, samples in sorted(runtime_samples.items())
        ],
    }
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output, delete=False) as stream:
        temporary_report = Path(stream.name)
        try:
            json.dump(report, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
        except BaseException:
            temporary_report.unlink(missing_ok=True)
            raise
    temporary_report.replace(output / "evaluation.json")
    return report


def _canonical_document(document: Document) -> DocumentResult:
    fields = defaultdict(dict)
    by_id = {observation.id: observation for observation in document.observations}
    for observation in document.observations:
        entity_fields = fields[observation.entity_id]
        if observation.field in entity_fields:
            raise ValueError(f"Duplicate field on entity {observation.entity_id}: {observation.field}")
        inputs = []
        if observation.derivation:
            for identifier in observation.derivation.inputs:
                cited = by_id[identifier]
                inputs.append(cited.field if cited.entity_id == observation.entity_id
                              else f"{cited.entity_id}/{cited.field}")
        entity_fields[observation.field] = Reading(
            observation.value, observation.state, tuple(observation.alternatives),
            tuple(sorted({ref.page for ref in observation.evidence})), observation.raw,
            observation.origin, tuple(inputs),
        )
    entities = tuple(
        EntityResult(entity.id, entity.kind, fields[entity.id], entity.parent_id)
        for entity in sorted(document.entities, key=lambda row: (row.page, row.ordinal))
    )
    approval = "review" if document.approval == "needs_review" else document.approval
    return DocumentResult(document.doc_type, tuple(document.scope), entities, approval,
                          tuple(issue.code for issue in document.issues))


def _source_result(documents: list[Document]) -> SourceResult:
    if not documents:
        return SourceResult("error", detail="Extraction emitted no documents")
    processed = tuple(sorted({p for doc in documents for p in doc.coverage.processed_pages}))
    skipped = tuple(sorted({p for doc in documents for p in doc.coverage.skipped_pages}))
    incomplete = tuple(sorted({p for doc in documents for p in doc.coverage.incomplete_pages}))
    status = "incomplete" if any(doc.validation == "incomplete" for doc in documents) else "completed"
    return SourceResult(
        status=status, documents=tuple(_canonical_document(doc) for doc in documents),
        page_count=documents[0].source.page_count, processed_pages=processed, skipped_pages=skipped,
        incomplete_pages=incomplete,
        suspected_capture=any(doc.coverage.suspected_capture for doc in documents),
        issues=tuple(sorted({issue.code for doc in documents for issue in doc.issues})),
        runtime={"runs": [
            {"scope": doc.scope, "job_id": doc.run.job_id, "pipeline": doc.run.pipeline,
             "model": doc.run.model, "model_revision": doc.run.revision,
             "pipeline_version": doc.run.pipeline_version, "schema_version": doc.run.schema_version,
             "config_hash": doc.run.config_hash, "runtime": doc.run.runtime,
             "settings": doc.run.settings, "calls": doc.run.calls}
            for doc in documents
        ]},
    )


def _with_job_artifacts(result: SourceResult, job_root: Path, error_code: str | None) -> SourceResult:
    """Recover source-wide coverage and per-page calls from the local stage artifacts."""
    job_file = job_root / "job.json"
    job = json.loads(job_file.read_text(encoding="utf-8")) if job_file.is_file() else {}
    ingestion_file = job_root / "ingestion.json"
    ingestion = (json.loads(ingestion_file.read_text(encoding="utf-8"))
                 if ingestion_file.is_file() else {})
    readings = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((job_root / "readings").glob("*.json"))
    ]
    skipped = set(result.skipped_pages) | set(ingestion.get("skipped_pages", []))
    incomplete = set(result.incomplete_pages) | {r["page"] for r in readings if r["incomplete"]}
    status = result.status
    if job.get("status") == "incomplete" or (error_code == "no_extraction" and incomplete):
        status = "incomplete"
    reader_issues = {issue["code"] for reading in readings for issue in reading["issues"]}
    return replace(
        result, status=status, skipped_pages=tuple(sorted(skipped)),
        incomplete_pages=tuple(sorted(incomplete)),
        page_count=ingestion.get("source", {}).get("page_count", result.page_count),
        suspected_capture=result.suspected_capture or any(r["suspected_cutoff"] for r in readings),
        issues=tuple(sorted(set(result.issues) | reader_issues)),
        runtime={**result.runtime, "job_id": job_root.name, "error_code": error_code,
                 "attempted_pages": [r["page"] for r in readings],
                 "calls_by_page": {str(r["page"]): r["calls"] for r in readings}},
    )


def run(manifest: Path, output: Path, settings: Settings) -> dict:
    """Evaluate the production pipeline. Inference locking belongs to the runtime."""
    from .config import ProcessingError, Settings
    from .models import runtime_info
    from .pipeline import extract, extraction_fingerprint

    def run_case(source, artifacts, overrides):
        effective = Settings.model_validate({**settings.model_dump(), **overrides})
        job_id = uuid4().hex
        job_root = effective.state_dir.resolve() / "jobs" / job_id
        try:
            documents = extract(source, artifacts, effective, job_id=job_id)
        except ProcessingError as exc:
            return _with_job_artifacts(SourceResult(exc.code, detail=str(exc)), job_root, exc.code)
        snapshot = [document.model_dump(mode="json") for document in documents]
        (artifacts / "result.json").write_text(
            json.dumps(snapshot, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return _with_job_artifacts(_source_result(documents), job_root, None)

    runtime = runtime_info(settings)
    configuration = {
        **settings.content_settings(), "model": runtime["model"], "revision": runtime["revision"],
        "versions": runtime["versions"], "components_ready": runtime["ready"],
        "base_config_hash": extraction_fingerprint(settings, runtime),
    }
    return evaluate_manifest(manifest, output, run_case, configuration=configuration)
