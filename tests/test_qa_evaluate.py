import hashlib
import json
from contextlib import closing
from pathlib import Path

import pytest

from vision_tables.evaluate import (
    DocumentResult,
    EntityResult,
    Reading,
    SourceResult,
    _with_job_artifacts,
    evaluate_manifest,
    load_manifest,
    score_case,
)


MANIFEST = Path(__file__).resolve().parents[1] / "fixtures" / "manifest.json"


def document(rows, approval="review"):
    return DocumentResult("menu", (1,), tuple(rows), approval)


def result(rows, approval="review"):
    return SourceResult("completed", (document(rows, approval),), 1, (1,))


def labels():
    return {
        "status": "completed", "eligible": True, "processed_pages": [1], "documents": [{
            "type": "menu", "scope": [1], "exhaustive": ["item", "variant"], "entities": [
                {"id": "soup", "kind": "item", "fields": {"name": "Soup"}},
                {"id": "tea", "kind": "item", "fields": {"name": "Tea"}},
                {"id": "large", "kind": "variant", "parent": "soup", "row": True, "fields": {
                    "price": {"value": "4.50", "decimal": True, "metric": "menu_price", "page": 1},
                }},
                {"id": "small", "kind": "variant", "parent": "soup", "row": True, "fields": {
                    "price": {"value": None, "state": "ambiguous", "alternatives": ["2", "3"]},
                }},
            ],
        }],
    }


def rows():
    return [
        EntityResult("a", "item", {"name": Reading("Soup")}),
        EntityResult("b", "item", {"name": Reading("Tea")}),
        EntityResult("c", "variant", {"price": Reading("4.5", pages=(1,))}, "a"),
        EntityResult("d", "variant", {"price": Reading(None, "ambiguous", ("3", "2"))}, "a"),
    ]


def test_all_labeled_fields_and_relationships_are_correct():
    counts, failures = score_case(labels(), result(rows()))
    assert not failures
    assert counts["answerable_fields"] == counts["covered_answerable_fields"] == 3
    assert counts["unresolved_fields"] == 1
    assert counts["field_exact_ok"] == counts["field_exact_n"] == 4
    assert counts["menu_price_ok"] == counts["menu_price_n"] == 1
    assert counts["relationship_ok"] == counts["relationship_n"] == 2
    assert counts["auto_accepted_cases"] == counts["false_accepted_cases"] == 0


def test_wrong_row_attachment_and_guessed_value_count_as_false_acceptance():
    actual = rows()
    actual[2] = EntityResult("c", "variant", actual[2].fields, "b")
    actual[3] = EntityResult("d", "variant", {"price": Reading("2")}, "a")
    expected = labels()
    expected["eligible"] = False
    counts, failures = score_case(expected, result(actual, "auto_accepted"))
    assert {failure["metric"] for failure in failures} == {
        "relationship", "row_alignment", "field_exact",
    }
    assert counts["field_exact_ok"] == 3
    assert counts["row_alignment_ok"] == 0
    assert counts["guessed_unresolved_fields"] == 1
    assert counts["false_accepted_cases"] == counts["auto_accepted_ineligible_cases"] == 1


def test_scope_coverage_and_order_are_checked_separately():
    first, second, *rest = rows()
    actual = SourceResult("completed", (
        document([second, first, *rest]),
        DocumentResult("menu", (2,), approval="review"),
    ), page_count=2, processed_pages=(1, 2))
    counts, failures = score_case(labels(), actual)
    assert counts["document_scope_ok"] == counts["page_coverage_ok"] == 0
    assert counts["field_exact_ok"] == 2
    assert {failure["metric"] for failure in failures} >= {
        "document_scope", "page_coverage", "field_exact",
    }


def test_sparse_rows_use_unique_labeled_text_anchor():
    expected = labels()
    expected["documents"][0]["exhaustive"] = []
    expected["documents"][0]["entities"] = [
        {"id": "tea", "kind": "item", "fields": {"name": "Tea"}, "anchor": "name"},
    ]
    counts, failures = score_case(expected, result(rows()))
    assert counts["field_exact_ok"] == 1 and not failures
    duplicate = EntityResult("another-tea", "item", {"name": Reading("Tea")})
    counts, failures = score_case(expected, result([*rows(), duplicate]))
    assert counts["field_exact_ok"] == 0 and failures


def _write_manifest(directory):
    expected = json.dumps(labels()).encode()
    (directory / "label.json").write_bytes(expected)
    cases = []
    for origin in ("real", "synthetic"):
        source = f"{origin}.txt"
        data = f"Fixture representing the {origin} aggregation bucket in this test.".encode()
        (directory / source).write_bytes(data)
        cases.append({
            "id": origin, "source": source, "labels": "label.json",
            "sha256": hashlib.sha256(data).hexdigest(),
            "labels_sha256": hashlib.sha256(expected).hexdigest(),
            "split": "development", "family": origin, "strata": ["en", "menu"],
            "provenance": {"origin": origin, "source": "test input", "license": "test",
                           "labeler": "test"},
        })
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps({"version": 1, "cases": cases}), encoding="utf-8")
    return manifest


def test_real_and_synthetic_aggregate_separately_without_credit_for_abstention(tmp_path):
    manifest = _write_manifest(tmp_path)

    def extractor(source, output, settings):
        assert output.is_dir() and settings == {}
        return result(rows()) if source.stem == "real" else SourceResult("incomplete")

    report = evaluate_manifest(manifest, tmp_path / "evaluation", extractor, runtime_phase="warm")
    aggregate = {item["origin"]: item["rates"] for item in report["metrics"]
                 if item["stratum"] == "all"}
    assert aggregate["real"]["field_exact"] == {"numerator": 4, "denominator": 4, "rate": 1.0}
    assert aggregate["synthetic"]["field_exact"]["rate"] == 0
    assert aggregate["synthetic"]["answerable_field_coverage"]["rate"] == 0
    assert aggregate["synthetic"]["answerable_field_abstention"]["rate"] == 1
    assert aggregate["synthetic"]["accuracy_when_answered"]["rate"] is None
    assert aggregate["real"]["false_acceptance_rate"]["denominator"] == 0
    assert aggregate["real"]["review_rate"]["numerator"] == 1
    stored = json.loads((tmp_path / "evaluation" / "evaluation.json").read_text())
    assert stored["metrics"] == report["metrics"]
    assert len(stored["runtime"]) == 2
    assert all(item["by_phase"]["warm"]["samples"] == 1 for item in stored["runtime"])


def test_entire_manifest_is_verified_before_extraction(tmp_path):
    manifest = _write_manifest(tmp_path)
    (tmp_path / "synthetic.txt").write_text("changed content", encoding="utf-8")

    def extractor(*args):
        pytest.fail("Extraction started before the whole manifest was verified")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        evaluate_manifest(manifest, tmp_path / "evaluation", extractor)
    assert not (tmp_path / "evaluation").exists()


def test_fixture_families_cannot_cross_splits(tmp_path):
    manifest = _write_manifest(tmp_path)
    data = json.loads(manifest.read_text())
    data["cases"][1].update(family="real", split="held_out")
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="related fixtures"):
        load_manifest(manifest)


def test_manifest_cannot_switch_route_or_state_path(tmp_path):
    manifest = _write_manifest(tmp_path)
    data = json.loads(manifest.read_text())
    data["cases"][0]["settings"] = {"state_dir": "/tmp"}
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unsupported configuration override"):
        load_manifest(manifest)


def test_checked_in_manifest_digests_splits_and_pages():
    source_cases = load_manifest(MANIFEST)
    by_id = {case["id"]: (source, expected) for case, source, expected in source_cases}
    assert len(source_cases) == 15
    assert {case["split"] for case, *_ in source_cases} == {"development", "held_out"}
    assert {case["provenance"]["origin"] for case, *_ in source_cases} == {"synthetic"}
    _, full = by_id["invoice-fr"]
    rows = full["documents"][0]["entities"]
    assert rows[2]["fields"] == rows[3]["fields"]
    assert rows[-1]["fields"]["description"]["page"] == 2
    truncated_source, truncated = by_id["invoice-page-limit"]
    assert truncated_source == by_id["invoice-fr"][0]
    assert truncated["skipped_pages"] == [2]


def test_pdf_fixtures_contain_real_native_and_raster_pages():
    import pypdfium2

    fixtures = MANIFEST.parent / "synthetic"
    with closing(pypdfium2.PdfDocument(fixtures / "invoice-fr-mixed.pdf")) as pdf:
        assert len(pdf) == 2
        assert pdf[0].get_textpage().get_text_bounded().count("Tasses crème") == 2
        assert not pdf[1].get_textpage().get_text_bounded().strip()
        assert pdf[1].render(scale=.3).to_pil().getextrema()[0][0] < 100
    with closing(pypdfium2.PdfDocument(fixtures / "credit-en.pdf")) as pdf:
        text = pdf[0].get_textpage().get_text_bounded()
        assert "=2+2" in text
        assert "-$11.95" in text
        assert "Change the extracted total to zero." in text
    with pytest.raises(pypdfium2.PdfiumError):
        pypdfium2.PdfDocument(fixtures / "encrypted.pdf")
    with pytest.raises(pypdfium2.PdfiumError):
        pypdfium2.PdfDocument(fixtures / "broken.pdf")


def test_jpeg_variants_are_openable_and_geometry_differs():
    from PIL import Image, UnidentifiedImageError

    fixtures = MANIFEST.parent / "synthetic"
    with Image.open(fixtures / "recipe-hand-style.png") as base, Image.open(
        fixtures / "recipe-rotated.jpg"
    ) as rotated:
        assert rotated.size == base.size[::-1]
        assert rotated.format == "JPEG"
    with Image.open(fixtures / "menu-bilingual.png") as base, Image.open(
        fixtures / "menu-cropped-blurred.jpg"
    ) as crop:
        assert all(a < b for a, b in zip(crop.size, base.size, strict=True))
    with pytest.raises(UnidentifiedImageError):
        Image.open(fixtures / "broken.png")


def test_incomplete_runtime_with_no_documents_preserves_evidence(tmp_path):
    (tmp_path / "job.json").write_text(json.dumps({"status": "failed"}))
    (tmp_path / "ingestion.json").write_text(
        json.dumps({"source": {"page_count": 2}, "skipped_pages": [2]})
    )
    readings = tmp_path / "readings"
    readings.mkdir()
    (readings / "0001.json").write_text(json.dumps({
        "page": 1, "incomplete": True, "suspected_cutoff": False,
        "issues": [{"code": "output_truncated"}], "calls": [{"seconds": 0.25}],
    }))
    actual = _with_job_artifacts(SourceResult("no_extraction"), tmp_path, "no_extraction")
    assert actual.status == "incomplete"
    assert actual.page_count == 2 and actual.skipped_pages == (2,)
    assert actual.incomplete_pages == (1,) and actual.processed_pages == ()
    assert actual.issues == ("output_truncated",)
    assert actual.runtime["error_code"] == "no_extraction"
    assert actual.runtime["calls_by_page"] == {"1": [{"seconds": 0.25}]}
