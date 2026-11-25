import errno
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pypdfium2 as pdf
import pytest
from PIL import Image

from vision_tables import runtime_local, vision
from vision_tables.config import ProcessingError, Settings
from vision_tables.vision_geometry import invert, point

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INVOICE = FIXTURES / "feasibility/invoice.png"


def test_image_import_is_immutable_and_exif_mapping(tmp_path):
    source = tmp_path / "misleading.png"
    image = Image.new("RGB", (600, 300), "white")
    exif = image.getexif()
    exif[274] = 6
    image.save(source, format="JPEG", exif=exif)
    job = tmp_path / "job"
    job.mkdir()
    settings = Settings(render_pixels=45_000)
    result = vision.inspect_source(source, job, settings)
    page, = result.pages
    assert result.source.media_type == "image/jpeg"
    assert (result.source.size_bytes, result.source.sha256) == (
        source.stat().st_size, hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    assert (job / result.source.original_path).read_bytes() == source.read_bytes()
    assert (page.width, page.height, page.image_width, page.image_height) == (600, 300, 150, 300)
    assert point(page.image_to_original, 0, 0) == (0, 300)
    assert point(page.image_to_original, 150, 300) == (600, 0)
    assert point(invert(page.image_to_original), 600, 0) == (150, 300)
    with pytest.raises(ProcessingError, match="already has an original"):
        vision.inspect_source(source, job, settings)


def test_native_pdf_regions_and_mixed_page_scope(tmp_path):
    path = FIXTURES / "synthetic/invoice-fr-mixed.pdf"
    full = tmp_path / "full"
    full.mkdir()
    result = vision.inspect_source(path, full, Settings())
    assert [p.text_layer for p in result.pages] == ["usable", "absent"]
    assert all(r.page == 1 and r.box.precision == "native_text" for r in result.regions)
    assert any("Facture" in r.text for r in result.regions)
    assert all(0 <= r.box.xyxy[0] < r.box.xyxy[2] <= result.pages[0].width for r in result.regions)
    limited = tmp_path / "limited"
    limited.mkdir()
    result = vision.inspect_source(path, limited, Settings(max_pages=1))
    assert result.source.page_count == 2
    assert result.skipped_pages == result.source_skipped_pages == [2]
    assert any(issue.code == "page_limit" for issue in result.issues)


def test_pdf_rotation_and_crop_use_original_top_left(tmp_path):
    source = tmp_path / "rotation.pdf"
    with pdf.PdfDocument(FIXTURES / "synthetic/invoice-en.pdf") as doc:
        page = doc[0]
        page.set_cropbox(10, 20, 600, 770)
        page.set_rotation(90)
        page.close()
        doc.save(source)
    job = tmp_path / "job"
    job.mkdir()
    page, = vision.inspect_source(source, job, Settings()).pages
    assert (page.width, page.height, page.unit) == (590, 750, "pt")
    assert page.image_width > page.image_height
    assert point(page.image_to_original, 0, 0) == pytest.approx((0, 750), abs=0.001)
    assert point(page.image_to_original, page.image_width, page.image_height) == pytest.approx((590, 0), abs=0.001)


@pytest.mark.parametrize("name,code", [
    ("broken.png", "corrupt_image"), ("broken.pdf", "corrupt_pdf"), ("encrypted.pdf", "encrypted_pdf"),
])
def test_invalid_source_reasons(tmp_path, name, code):
    with pytest.raises(ProcessingError) as error:
        vision.inspect_source(FIXTURES / "synthetic" / name, tmp_path, Settings())
    assert error.value.code == code


def test_source_limits_and_heic(tmp_path):
    with pytest.raises(ProcessingError) as error:
        vision.inspect_source(INVOICE, tmp_path, Settings(max_bytes=16))
    assert error.value.code == "oversized_input"
    assert not (tmp_path / "original").exists()
    fake_heic = tmp_path / "source"
    fake_heic.write_bytes(b"\0\0\0\x18ftypheic\0\0\0\0mif1heic")
    with pytest.raises(ProcessingError) as error:
        vision.inspect_source(fake_heic, tmp_path, Settings())
    assert error.value.code == "unsupported_heic"
    with pytest.raises(ProcessingError) as error:
        vision.inspect_source(INVOICE, tmp_path, Settings(max_pixels=10))
    assert error.value.code == "source_pixel_limit"


def reply(raw="37,25", quote="TOTAL 37,25 EUR", detail=None, region=None, **metadata):
    fields = [dict(n="total", v=raw, q=quote, g=region)]
    if detail:
        fields.append(detail)
    accepted = {"d": "invoice", "cut": False, "r": [dict(k="invoice", i="a", p=None, f=fields)]}
    return {"raw": json.dumps(accepted), "generated_tokens": 30, "finish_reason": "stop", **metadata}


def test_valid_json_overflow_and_reread_disagreement(tmp_path, monkeypatch):
    settings = Settings(doc_type="invoice", max_tokens=32, retries=1, max_crops=0)
    ingestion = vision.inspect_source(INVOICE, tmp_path, settings)
    responses = iter([reply(finish_reason="length"), reply(raw="57,25", quote="TOTAL 57,25 EUR")])
    monkeypatch.setattr(runtime_local, "call", lambda *unused: next(responses))
    page, = ingestion.pages
    result = vision.read_page(page, [], tmp_path, settings)
    total = result.records[0].fields["total"]
    assert result.incomplete and len(result.calls) == 2
    assert {issue.code for issue in result.issues} == {"model_token_limit", "reread_disagreement"}
    assert total.state == "ambiguous" and total.raw is None and total.alternatives == ["37,25", "57,25"]
    assert (tmp_path / "attempts/p1-qwen-0/attempt.json").is_file()


def test_malformed_forged_evidence_and_unsupported_model_field(tmp_path, monkeypatch):
    settings = Settings(doc_type="invoice", retries=2, max_crops=0)
    page, = vision.inspect_source(INVOICE, tmp_path, settings).pages
    wrong_region = reply(region="model-bbox-123")
    extra_geometry = reply()
    extra_geometry["raw"] = extra_geometry["raw"].replace('"g": null', '"g": null, "xyxy": [1,2,3,4]')
    duplicate = reply()
    duplicate["raw"] = duplicate["raw"].replace('"cut": false', '"cut": true, "cut": false')
    answers = iter([wrong_region, extra_geometry, duplicate])
    monkeypatch.setattr(runtime_local, "call", lambda *unused: next(answers))
    result = vision.read_page(page, [], tmp_path, settings)
    assert result.incomplete and not result.records and len(result.calls) == 3
    assert all(call["error"] == "invalid_model_reply" for call in result.calls)


def test_native_quote_matching_and_crop_transform(tmp_path, monkeypatch):
    settings = Settings(doc_type="invoice", retries=0, max_crops=1)
    fixture = FIXTURES / "synthetic/invoice-en.pdf"
    ingestion = vision.inspect_source(fixture, tmp_path, settings)
    page, = ingestion.pages
    region = next(r for r in ingestion.regions if "PW-318" in r.text)
    source_reply = reply(None, "", dict(n="document_number", v="PW-318", q="Invoice PW-318", g=region.id))
    crop_reply = reply(raw="318", quote="Invoice PW-318", region=region.id)
    calls = []

    def run(_, request, *unused):
        calls.append(request)
        return source_reply if len(calls) == 1 else crop_reply

    monkeypatch.setattr(runtime_local, "call", run)
    result = vision.read_page(page, ingestion.regions, tmp_path, settings)
    assert calls[0]["image"] is None and calls[1]["image"].endswith(".png")
    assert result.records[0].fields["document_number"].region_id == region.id
    crop, = result.regions
    assert crop.box.precision == "crop"
    assert crop.box.xyxy[:2] == pytest.approx(point(crop.crop_to_original, 0, 0), abs=0.001)
    assert (tmp_path / crop.artifact_path).is_file()
    assert crop.artifact_sha256 == hashlib.sha256((tmp_path / crop.artifact_path).read_bytes()).hexdigest()


def test_native_invented_amount_abstains(tmp_path, monkeypatch):
    settings = Settings(doc_type="invoice", retries=0, max_crops=0)
    ingestion = vision.inspect_source(FIXTURES / "synthetic/invoice-en.pdf", tmp_path, settings)
    page, = ingestion.pages
    guess = reply(raw="7650", quote="Total 7650", region=ingestion.regions[0].id)
    monkeypatch.setattr(runtime_local, "call", lambda *unused: guess)
    result = vision.read_page(page, ingestion.regions, tmp_path, settings)
    total = result.records[0].fields["total"]
    assert total.raw is None and total.state == "unreadable" and total.region_id is None
    assert any(issue.code == "unsupported_quote" for issue in result.issues)


@pytest.mark.skipif(sys.platform != "darwin", reason="Uses the macOS process sandbox")
@pytest.mark.parametrize("outer", [False, True])
def test_real_child_network_denied_inside_each_sandbox_path(tmp_path, outer):
    python = tmp_path / "direct-env/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    (tmp_path / "models/qwen").mkdir(parents=True)
    (tmp_path / "runtime_worker.py").write_text(
        "import json,socket,sys\n"
        "with socket.socket() as sock:\n"
        "    sock.settimeout(2)\n"
        "    result = sock.connect_ex(('1.1.1.1',443))\n"
        "with open(sys.argv[2],'w') as dest: json.dump({'errno':result},dest)\n"
    )
    code = (
        "import json, sys; from pathlib import Path; from vision_tables import runtime_local; "
        "from vision_tables.config import Settings; p=Path(sys.argv[1]); "
        "runtime_local.__file__=str(p/'runtime_local.py'); runtime_local.runtime_info=lambda *a: {'ready': True}; "
        "print(json.dumps(runtime_local.call('qwen', {}, p/'attempt', Settings(state_dir=p, lock_file=p/'lock'))))"
    )
    command = [sys.executable, "-c", code, str(tmp_path)]
    env = os.environ.copy()
    env.pop("VT_VERIFY_OUTER_SANDBOX", None)
    if outer:
        command = ["/usr/bin/sandbox-exec", "-p", "(version 1) (allow default) (deny network*)", *command]
        env["VT_VERIFY_OUTER_SANDBOX"] = "1"
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=True, timeout=15)
    assert json.loads(result.stdout)["errno"] in (errno.EPERM, errno.EACCES)


def test_hybrid_reports_incomplete_pins_independently(tmp_path):
    require = vision.hybrid_info(Settings(state_dir=tmp_path, pipeline="hybrid"))
    assert not require["ready"] and "layout/inference.pdiparams" in require["reason"]


def test_hybrid_adapter_maps_parser_boxes_and_targets_native_resolution_crop(tmp_path, monkeypatch):
    settings = Settings(doc_type="invoice", pipeline="hybrid", render_pixels=84_000, retries=0, max_crops=1)
    page, = vision.inspect_source(INVOICE, tmp_path, settings).pages
    monkeypatch.setattr(vision, "hybrid_info", lambda _: {"ready": True, "model": "PaddleOCR-VL-1.6"})
    region = "p1-parser-0"
    invoked = []

    def run(kind, request, *unused):
        invoked.append((kind, request))
        if kind == "paddle":
            assert request["max_regions"] == 32
            return {"width": page.image_width, "height": page.image_height, "device": "cpu",
                    "omitted_regions": 1, "layout": {"boxes": ["synthetic"]},
                    "blocks": [{"block_bbox": [20, 30, 180, 60], "block_label": "text",
                                "block_content": "Référence VTX-4187"}]}
        if len(invoked) == 2:
            return reply(None, "", dict(n="document_number", v="VTX-4187", q="Référence VTX-4187", g=region))
        return reply()

    monkeypatch.setattr(runtime_local, "call", run)
    result = vision.read_page(page, [], tmp_path, settings)
    assert [kind for kind, _ in invoked] == ["paddle", "qwen", "qwen"]
    assert result.incomplete and any(issue.code == "parser_region_limit" for issue in result.issues)
    parsed, crop = result.regions
    assert parsed.box.xyxy == (40, 60, 360, 120)
    assert crop.box.xyxy == (16, 36, 384, 144)
    assert crop.crop_to_original == (1, 0, 0, 1, 16, 36)
    assert result.records[0].fields["total"].region_id == crop.id
