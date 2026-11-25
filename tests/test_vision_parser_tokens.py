import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vision_tables import models, pipeline, runtime_local, vision
from vision_tables.config import ProcessingError, Settings
from vision_tables.contracts import Run
from vision_tables.domain import assemble
from vision_tables.runtime_worker import MeasuredRecognition

INVOICE = Path(__file__).resolve().parents[1] / "fixtures/feasibility/invoice.png"


class NativeRecognizer:
    """Expose the pinned upstream sequence contract at generate, then decode to text."""

    def __init__(self, generated):
        self.infer = SimpleNamespace(
            generation_config=SimpleNamespace(trunc_input=True, eos_token_id=2),
            generate=lambda inputs, max_new_tokens: next(generated),
        )

    def predict(self, items, *, max_new_tokens):
        for item in items:
            inputs = {"input_ids": SimpleNamespace(shape=[1, 20])}
            self.infer.generate(inputs, max_new_tokens=max_new_tokens)
            yield item["result"]


def sequence(tokens):
    return SimpleNamespace(tolist=lambda: [tokens]), object()


def test_worker_reads_native_ids_and_restores_recognition_method():
    native = NativeRecognizer(iter([sequence([11, 2]), sequence([11, 12, 13, 14]), None]))
    original = native.infer.generate
    measured = MeasuredRecognition(native)
    assert list(measured.predict([{"result": "abc"}, {"result": "yes"}], max_new_tokens=4)) == ["abc", "yes"]
    assert list(measured.predict([{"result": "unknown"}], max_new_tokens=4)) == ["unknown"]
    assert native.infer.generate is original
    assert measured.report() == {"requested_crops": 3, "returned_crops": 3, "crops": [
        {"crop_index": 1, "generated_tokens": 2, "token_limit": 4, "cap_hit": False, "eos_seen": True},
        {"crop_index": 2, "generated_tokens": 4, "token_limit": 4, "cap_hit": True, "eos_seen": False},
        {"crop_index": 3, "generated_tokens": None, "token_limit": 4, "cap_hit": None, "eos_seen": None},
    ]}


def token_report(count, cap_hit, eos_seen, *, requested=1):
    return {"requested_crops": requested, "returned_crops": 1, "crops": [
        {"crop_index": 1, "generated_tokens": count, "token_limit": 32, "cap_hit": cap_hit, "eos_seen": eos_seen},
    ]}


@pytest.mark.parametrize("telemetry,omitted,issue_codes", [
    (token_report(18, False, True), 0, set()),
    (token_report(32, True, False), 0, {"parser_token_limit"}),
    (token_report(32, True, True), 0, {"parser_token_limit"}),
    (None, 0, {"parser_token_telemetry_missing"}),
    (token_report(18, False, True, requested=2), 0, {"parser_token_telemetry_missing"}),
    (token_report(18, False, False), 0, {"parser_token_telemetry_missing"}),
    (token_report(32, False, True), 0, {"parser_token_telemetry_missing"}),
    (token_report(32, True, False), 2, {"parser_token_limit", "parser_region_limit"}),
])
def test_hybrid_page_surfaces_token_coverage_without_losing_regions(tmp_path, monkeypatch, telemetry, omitted, issue_codes):
    ingestion, reading = _read(tmp_path, monkeypatch, telemetry, omitted=omitted)
    assert reading.incomplete is bool(issue_codes)
    assert {issue.code for issue in reading.issues} == issue_codes
    assert reading.regions[0].text == "TOTAL 37,25 EUR"
    assert reading.records[0].fields["total"].region_id == reading.regions[0].id
    assert reading.calls[0]["recognition_tokens"] == telemetry
    assert json.loads((tmp_path / "attempts/p1-paddle/attempt.json").read_text()) == reading.calls[0]
    assert reading.calls[0]["status"] == ("incomplete" if issue_codes else "parsed")
    run = Run(job_id="test", pipeline="hybrid", model="test", revision="test", runtime={}, settings={},
              config_hash="0" * 64, calls=reading.calls)
    document = assemble(ingestion, [reading], [1], run, "doc-test")
    assert document.coverage.incomplete_pages == ([1] if issue_codes else [])
    assert document.run.calls[0]["recognition_tokens"] == telemetry
    assert document.regions[0].text == "TOTAL 37,25 EUR"


def test_parser_geometry_and_context_omissions_preserve_available_text(tmp_path, monkeypatch):
    extra = {"block_bbox": [4, 4, 4, 4], "block_label": "text", "block_content": "x" * 9_500}
    _, reading = _read(tmp_path, monkeypatch, token_report(18, False, True), extra=extra)
    assert reading.incomplete
    assert {issue.code for issue in reading.issues} == {"parser_content_incomplete", "parser_context_limit"}
    assert reading.regions[1].box is None and reading.regions[1].text == extra["block_content"]
    assert reading.calls[0]["context_omitted_regions"] == 1


@pytest.mark.parametrize("label,content", [
    ("text", ""), ("text", " \n\t"), ("table", "<table><tr><td> </td></tr></table>"),
    ("chart", ""), ("seal", ""),
])
def test_blank_text_bearing_block_preserves_box_and_marks_coverage_incomplete(tmp_path, monkeypatch, label, content):
    extra = {"block_bbox": [20, 100, 180, 140], "block_label": label, "block_content": content}
    telemetry = token_report(9, False, True, requested=2)
    telemetry["returned_crops"] = 2
    telemetry["crops"].append(dict(token_report(1, False, True)["crops"][0], crop_index=2))
    ingestion, reading = _read(tmp_path, monkeypatch, telemetry, extra=extra)

    good, unread = reading.regions
    assert (unread.id, unread.kind, unread.text) == ("p1-parser-1", f"parser_{label}", "")
    assert unread.box.xyxy == tuple(extra["block_bbox"])
    assert unread.box.precision == "parser_region"
    assert reading.incomplete and {i.code for i in reading.issues} == {"parser_content_incomplete"}
    parser_call, qwen_call = reading.calls
    assert parser_call["status"] == "incomplete" and parser_call["degraded_blocks"] == 1
    assert parser_call["token_telemetry_complete"] and parser_call["token_limit_hits"] == 0
    assert qwen_call["region_ids"] == [good.id]
    run = Run(job_id="test", pipeline="hybrid", model="test", revision="test", runtime={}, settings={},
              config_hash="0" * 64, calls=reading.calls)
    document = assemble(ingestion, [reading], [1], run, "doc-test")
    assert document.coverage.incomplete_pages == [1] and unread in document.regions
    assert any(obs.field == "total" and obs.value == "37.25" for obs in document.observations)


@pytest.mark.parametrize("label", ["image", "header_image", "footer_image"])
def test_blank_non_text_image_block_does_not_degrade_coverage(tmp_path, monkeypatch, label):
    extra = {"block_bbox": [20, 100, 180, 140], "block_label": label, "block_content": ""}
    _, reading = _read(tmp_path, monkeypatch, token_report(9, False, True), extra=extra)
    assert not reading.incomplete and not reading.issues
    assert reading.calls[0]["status"] == "parsed" and reading.calls[0]["degraded_blocks"] == 0


def test_blank_only_parser_output_cannot_succeed_extraction(tmp_path, monkeypatch):
    settings = Settings(state_dir=tmp_path, doc_type="invoice", pipeline="hybrid", max_tokens=32, retries=0, max_crops=0)
    monkeypatch.setattr(models, "runtime_info", lambda _: {
        "ready": True, "model": "fixture", "revision": "fixture", "versions": {}, "components": {},
    })
    monkeypatch.setattr(vision, "hybrid_info", lambda _: {"ready": True, "model": "fixture"})

    def call(kind, request, *unused):
        assert kind == "paddle"
        bounds = [20, 100, 180, 140]
        return {"width": 800, "height": 420, "omitted_regions": 0, "layout": {"boxes": [{"coordinate": bounds}]},
                "blocks": [{"block_bbox": bounds, "block_label": "text", "block_content": "   "}],
                "recognition_tokens": token_report(1, False, True)}

    monkeypatch.setattr(runtime_local, "call", call)
    with pytest.raises(ProcessingError) as failure:
        pipeline.extract(INVOICE, tmp_path / "output", settings, job_id="blank-parser")
    assert failure.value.code == "no_extraction"
    job = tmp_path / "jobs" / "blank-parser"
    reading = json.loads((job / "readings" / "0001.json").read_text())
    assert reading["incomplete"] and not reading["records"]
    assert reading["regions"][0]["box"]["xyxy"] == [20, 100, 180, 140]
    assert {issue["code"] for issue in reading["issues"]} >= {"parser_content_incomplete", "no_parser_regions"}
    assert json.loads((job / "job.json").read_text())["status"] == "failed"


def _read(tmp_path, monkeypatch, telemetry, *, omitted=0, extra=None):
    settings = Settings(doc_type="invoice", pipeline="hybrid", max_tokens=32, retries=0, max_crops=0)
    ingestion = vision.inspect_source(INVOICE, tmp_path, settings)
    page, = ingestion.pages
    monkeypatch.setattr(vision, "hybrid_info", lambda _: {"ready": True, "model": "PaddleOCR-VL-1.6"})

    def call(kind, request, *unused):
        if kind == "paddle":
            assert request["max_tokens"] == 32
            return {"width": page.image_width, "height": page.image_height, "device": "cpu",
                    "omitted_regions": omitted, "layout": {"boxes": [{"coordinate": [20, 30, 180, 60]}]},
                    "blocks": [{"block_bbox": [20, 30, 180, 60], "block_label": "text",
                                "block_content": "TOTAL 37,25 EUR"}, *([extra] if extra else [])],
                    **({"recognition_tokens": telemetry} if telemetry is not None else {})}
        return {"generated_tokens": 20, "finish_reason": "stop", "raw": json.dumps({
            "d": "invoice", "cut": False, "r": [{"k": "invoice", "i": "a", "p": None,
            "f": [{"n": "total", "v": "37,25", "q": "TOTAL 37,25 EUR", "g": "p1-parser-0"}]}],
        })}

    monkeypatch.setattr(runtime_local, "call", call)
    return ingestion, vision.read_page(page, ingestion.regions, tmp_path, settings)
