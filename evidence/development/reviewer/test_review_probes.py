"""Independent reviewer probes for the integrated commit.

Run from the repository root:
    .venv/bin/python -m pytest -q evidence/development/reviewer/test_review_probes.py

Only the model subprocess (runtime_local.call) and the model-readiness check (models.runtime_info) are
replaced. Ingestion, the vision adapter, domain assembly/corrections, SQLite persistence and exports are
the production code. No network, no model weights, no private documents.

Each test asserts the behaviour the request/interfaces require. A failing test is a reproduced finding;
see docs/development/review.md for the finding it belongs to.
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from vision_tables import cli, exports, models, runtime_local
from vision_tables.store import Store

FAKE_INFO = {"ready": True, "model": "fake-qwen", "revision": "r0", "versions": {"mlx_vlm": "test"},
             "components": {}}


def _field(name, value, quote=None):
    return {"n": name, "v": value, "q": quote if quote is not None else (value or ""), "g": None}


def invoice_reply(total="10,00", lines=(("Bols", "4,00"), ("Tasses", "6,00")), extra=()):
    records = [{"k": "invoice", "i": "inv", "p": None,
                "f": [_field("supplier_name", "Atelier"), _field("document_number", "F-1"),
                      _field("currency", "EUR"), _field("issue_date", "17/03/2026"), _field("total", total),
                      *extra]}]
    for i, (name, amount) in enumerate(lines):
        records.append({"k": "line_item", "i": f"l{i}", "p": "inv",
                        "f": [_field("description", name), _field("line_total", amount)]})
    return {"d": "invoice", "cut": False, "r": records}


class FakeModel:
    """Serves queued replies; each reply is (payload, generated_tokens, finish_reason)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, kind, request, directory, settings):
        directory.mkdir(parents=True, exist_ok=True)
        self.calls += 1
        payload, tokens, finish = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        return {"raw": json.dumps(payload, ensure_ascii=False), "generated_tokens": tokens,
                "finish_reason": finish, "prompt_tokens": 10}


@pytest.fixture
def env(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("VT_STATE_DIR", raising=False)
    monkeypatch.setattr(models, "runtime_info", lambda settings: dict(FAKE_INFO))
    state = tmp_path / "state"

    def run(*argv):
        code = cli.main([*argv, "--state-dir", str(state), "--no-progress"])
        out = json.loads(capsys.readouterr().out)
        assert out["exit_code"] == code
        return code, out

    def model(*replies):
        fake = FakeModel(*replies)
        monkeypatch.setattr(runtime_local, "call", fake)
        return fake

    image = tmp_path / "in" / "invoice.png"
    image.parent.mkdir()
    picture = Image.new("RGB", (600, 400), "white")
    ImageDraw.Draw(picture).text((20, 20), "Facture F-1  Total 10,00 EUR", fill="black")
    picture.save(image)
    return type("Env", (), {"run": staticmethod(run), "model": staticmethod(model), "state": state,
                            "image": image, "tmp": tmp_path})


def _doc(out):
    return out["documents"][0]


# --- F1: lost export formats ----------------------------------------------------------------------
def test_pending_default_export_survives_a_later_single_format_export(env, monkeypatch):
    env.model((invoice_reply(), 50, "stop"))
    out_dir = env.tmp / "out"
    real_publish = exports.publish
    monkeypatch.setattr(exports, "publish", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    code, out = env.run("extract", str(env.image), "--output", str(out_dir))
    assert code == 8 and _doc(out)["export"]["state"] == "pending"
    monkeypatch.setattr(exports, "publish", real_publish)
    document_id, version = _doc(out)["document_id"], _doc(out)["version"]
    code, _ = env.run("export", "--document", document_id, "--format", "sqlite", "--output", str(out_dir))
    assert code == 0
    code, recovered = env.run("jobs", "recover")
    target = out_dir / document_id / f"v{version:04d}"
    # Required: the job's JSON/CSV/review publication is either done or still pending for recovery.
    pending = [e for e in Store(env.state).pending_exports() if e["output_dir"] == str(out_dir.resolve())]
    assert (target / "document.json").is_file() or pending, (
        "json/csv/review export silently dropped: row marked published, files never written; "
        f"files present={sorted(p.name for p in target.iterdir())}")


def test_pending_correction_export_survives_a_later_single_format_export(env, monkeypatch):
    env.model((invoice_reply(), 50, "stop"))
    out_dir = env.tmp / "out"
    code, first = env.run("extract", str(env.image), "--output", str(out_dir))
    document_id = _doc(first)["document_id"]
    store = Store(env.state)
    target_obs = next(o for o in store.get_document(document_id).observations if o.field == "supplier_name")
    corrections = env.tmp / "c.json"
    corrections.write_text(json.dumps({"document_id": document_id, "base_version": 1, "reviewer": "r",
                                       "changes": [{"observation_id": target_obs.id, "value": "Atelier B",
                                                    "raw": "Atelier B", "reason": "checked"}]}))
    real_publish = exports.publish
    monkeypatch.setattr(exports, "publish", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    code, applied = env.run("review", "apply", str(corrections), "--document", document_id, "--output", str(out_dir))
    assert code == 8 and applied["version"] == 2
    monkeypatch.setattr(exports, "publish", real_publish)
    code, _ = env.run("export", "--document", document_id, "--version", "2", "--format", "sqlite",
                      "--output", str(out_dir))
    assert code == 0
    code, recovered = env.run("jobs", "recover")
    target = out_dir / document_id / "v0002"
    pending = [e for e in store.pending_exports() if e["version"] == 2]
    assert (target / "document.json").is_file() or pending, (
        "corrected version's json/csv/review export lost: exports row marked published after a sqlite-only "
        f"export; recover reports {recovered['status']}; files={sorted(p.name for p in target.iterdir())}")


# --- F2: superseded provisional lineage still accepts corrections ----------------------------------
def test_correction_on_superseded_provisional_extraction_is_rejected(env):
    # First run: token budget exhausted -> incomplete -> provisional base v1.
    env.model((invoice_reply(), 2048, "length"), (invoice_reply(), 2048, "length"))
    code, first = env.run("extract", str(env.image), "--output", str(env.tmp / "o"))
    assert code == 3 and _doc(first)["validation"] == "incomplete"
    # Second run, same effective configuration, complete -> supersedes v1 as v2.
    env.model((invoice_reply(), 50, "stop"))
    code, second = env.run("extract", str(env.image), "--output", str(env.tmp / "o"))
    assert _doc(second)["action"] == "superseded" and _doc(second)["version"] == 2
    document_id = _doc(second)["document_id"]
    store = Store(env.state)
    v1 = store.get_document(document_id, 1)
    target = next(o for o in v1.observations if o.field == "supplier_name")
    corrections = env.tmp / "c.json"
    corrections.write_text(json.dumps({"document_id": document_id, "base_version": 1, "reviewer": "r",
                                       "changes": [{"observation_id": target.id, "value": "Atelier B",
                                                    "raw": "Atelier B", "reason": "checked"}]}))
    code, applied = env.run("review", "apply", str(corrections), "--document", document_id)
    # Required: the superseded provisional version is no longer a valid review base.
    assert code == 6 and applied["error"]["code"] == "stale_base", (
        f"correction accepted on superseded v1 -> v{applied.get('version')}; "
        f"reimport serves v{_doc(env.run('extract', str(env.image), '--output', str(env.tmp / 'o'))[1])['version']}")


# --- Integrated replay / correction behaviour (expected to pass) -----------------------------------
def test_integrated_replay_correction_and_consistent_exports(env):
    env.model((invoice_reply(total="=HYPERLINK(\"http://x\",\"10\")",
                             lines=(("Bols", "4,00"), ("Bols", "4,00"), ("@SUM(A1)", "2,00"))), 50, "stop"))
    out_dir = env.tmp / "out"
    code, first = env.run("extract", str(env.image), "--output", str(out_dir))
    doc = _doc(first)
    assert code == 0 and doc["action"] == "created" and doc["review_required"]
    code, again = env.run("extract", str(env.image), "--output", str(out_dir))
    assert _doc(again)["action"] == "reused" and _doc(again)["version"] == 1
    document_id = doc["document_id"]
    store = Store(env.state)
    current = store.get_document(document_id)
    rows = [e for e in current.entities if e.kind == "line_item"]
    assert len(rows) == 3  # identical legitimate rows stay distinct
    line = next(o for o in current.observations if o.entity_id == rows[0].id and o.field == "line_total")
    corrections = env.tmp / "c.json"
    corrections.write_text(json.dumps({"document_id": document_id, "base_version": 1, "reviewer": "r",
                                       "changes": [{"observation_id": line.id, "value": "5.00", "raw": "5,00",
                                                    "reason": "checked"}]}))
    code, applied = env.run("review", "apply", str(corrections), "--document", document_id)
    assert code == 0 and applied["version"] == 2
    code, replay = env.run("review", "apply", str(corrections), "--document", document_id)
    assert replay["replayed"] and replay["version"] == 2
    code, reimport = env.run("extract", str(env.image), "--output", str(out_dir))
    assert _doc(reimport)["action"] == "reused" and _doc(reimport)["version"] == 2
    v2 = store.get_document(document_id, 2)
    calc = next(o for o in v2.observations if o.field == "calculated_total")
    assert calc.value == "11.00" and line.id in " ".join(calc.derivation.inputs) or calc.value == "11.00"
    # JSON / CSV / SQLite consistency of version 2
    code, _ = env.run("export", "--document", document_id, "--version", "2", "--format", "all", "--output", str(out_dir))
    target = out_dir / document_id / "v0002"
    canonical = json.loads((target / "document.json").read_text())
    with (target / "tables" / "observations.csv").open(newline="") as fp:
        table = list(csv.DictReader(fp))
    conn = sqlite3.connect(target / "document.sqlite3")
    sql = {r[0]: r for r in conn.execute("SELECT id, value, state, origin FROM observations")}
    assert len(table) == len(canonical["observations"]) == len(sql)
    for obs in canonical["observations"]:
        row = next(r for r in table if r["observation_id"] == obs["id"])
        expected = "" if obs["value"] is None else str(obs["value"]).lower() if isinstance(obs["value"], bool) else obs["value"]
        assert row["value"].lstrip("'") == expected and sql[obs["id"]][2] == obs["state"]
    raw_cells = (target / "tables" / "observations.csv").read_text()
    assert ",=HYPERLINK" not in raw_cells and ",@SUM" not in raw_cells
    assert any(o["raw"].startswith("=HYPERLINK") for o in canonical["observations"] if o["raw"])


@pytest.mark.parametrize("budget", [2048, 4096])
def test_truncated_but_valid_reply_is_incomplete_not_success(env, budget):
    env.model((invoice_reply(), budget, None), (invoice_reply(), budget, None))
    code, out = env.run("extract", str(env.image), "--max-tokens", str(budget), "--output", str(env.tmp / "o"))
    assert code == 3 and out["status"] == "incomplete" and "model_token_limit" in _doc(out)["issue_codes"]


def test_unsupported_files_in_batch_are_visible(env):
    env.model((invoice_reply(), 50, "stop"))
    folder = env.tmp / "batch"
    folder.mkdir()
    (folder / "a.png").write_bytes(env.image.read_bytes())
    (folder / "scan.tiff").write_bytes(b"II*\x00fake")
    code, out = env.run("batch", str(folder), "--output", str(env.tmp / "bo"))
    skipped = [i for i in out["items"] if i["name"] == "scan.tiff"]
    assert skipped and skipped[0]["state"] == "skipped"
    # Recorded (not asserted): overall status/exit for a batch that skipped an input.
    (env.tmp / "batch-status.txt").write_text(f"{out['status']} {code}")
    print("BATCH_WITH_SKIPPED_INPUT", out["status"], code, out["counts"])


def test_date_order_borrowed_from_another_field_is_disclosed(env):
    reply = invoice_reply(extra=(_field("due_date", "15/04/2026"),))
    for f in reply["r"][0]["f"]:
        if f["n"] == "issue_date":
            f["v"] = f["q"] = "03/04/2026"
    env.model((reply, 50, "stop"))
    code, out = env.run("extract", str(env.image), "--output", str(env.tmp / "o"))
    document = Store(env.state).get_document(_doc(out)["document_id"])
    issue = next(o for o in document.observations if o.field == "issue_date")
    print("ISSUE_DATE", issue.state, issue.value, issue.alternatives, issue.reason)
    assert issue.state != "readable" or issue.reason, "03/04/2026 resolved without recording why"


# --- F3: native-only PDF reading drops non-text page content without a coverage signal ---------------
def _pdf_with_small_image(path: Path, fraction: float) -> None:
    import ctypes

    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    doc = pdfium.PdfDocument.new()
    page = doc.new_page(612, 792)
    for i, line in enumerate(["Facture F-1 Atelier", "Bols 4,00", "Tasses 6,00", "Total 10,00 EUR"]):
        obj = raw.FPDFPageObj_NewTextObj(doc.raw, b"Helvetica", 14)
        buf = ctypes.create_string_buffer((line + "\0").encode("utf-16-le"))
        raw.FPDFText_SetText(obj, ctypes.cast(buf, ctypes.POINTER(raw.FPDF_WCHAR)))
        raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 50, 740 - 24 * i)
        raw.FPDFPage_InsertObject(page.raw, obj)
    side = int((612 * 792 * fraction) ** 0.5)
    note = path.with_suffix(".jpg")
    picture = Image.new("RGB", (2 * side, 2 * side), "white")
    ImageDraw.Draw(picture).text((5, side), "REMISE -5,00", fill="black")
    picture.save(note)
    image = pdfium.PdfImage.new(doc)
    image.load_jpeg(str(note), inline=True)
    image.set_matrix(pdfium.PdfMatrix().scale(side, side).translate(350, 500))
    page.insert_obj(image)
    page.gen_content()
    doc.save(str(path))


def test_native_pdf_with_raster_note_is_not_read_text_only_without_a_signal(env):
    pdf_path = env.tmp / "in" / "native-note.pdf"
    _pdf_with_small_image(pdf_path, 0.05)  # a stamp / scanned handwritten note covering 5 % of the page
    reply = invoice_reply(total="10,00", lines=(("Bols", "4,00"), ("Tasses", "6,00")))
    for record in reply["r"]:
        for f in record["f"]:
            f["q"] = {"Atelier": "Facture F-1 Atelier", "F-1": "Facture F-1 Atelier", "EUR": "Total 10,00 EUR",
                      "10,00": "Total 10,00 EUR", "Bols": "Bols 4,00", "4,00": "Bols 4,00",
                      "Tasses": "Tasses 6,00", "6,00": "Tasses 6,00"}.get(f["v"], f["q"])
    reply["r"][0]["f"] = [f for f in reply["r"][0]["f"] if f["n"] != "issue_date"]
    seen = []
    fake = FakeModel((reply, 50, "stop"))

    def spy(kind, request, directory, settings):
        seen.append(request.get("image"))
        return fake(kind, request, directory, settings)
    import vision_tables.runtime_local as rl
    rl_call = rl.call
    rl.call = spy
    try:
        code, out = env.run("extract", str(pdf_path), "--type", "invoice", "--output", str(env.tmp / "o"))
    finally:
        rl.call = rl_call
    document = Store(env.state).get_document(_doc(out)["document_id"])
    print("NATIVE_NOTE", code, out["status"], document.pages[0].text_layer, seen, sorted({i.code for i in document.issues}))
    # Required: either the page image reaches the model, or the omitted raster content is visible as a coverage issue.
    assert any(seen) or document.pages[0].text_layer != "usable" or document.validation == "incomplete"


# --- F4: carried adapter/ingestion issues block approval permanently ---------------------------------
def _approve(env, document_id, version, changes=()):
    path = env.tmp / f"approve-{version}.json"
    path.write_text(json.dumps({"document_id": document_id, "base_version": version, "reviewer": "r",
                                "changes": list(changes), "approve": True}))
    return env.run("review", "apply", str(path), "--document", document_id)


def test_resolved_reread_disagreement_can_be_approved(env):
    first = invoice_reply()
    first["r"][0]["f"] = [f if f["n"] != "total" else {**f, "v": None} for f in first["r"][0]["f"]]
    second = invoice_reply()
    second["r"][0]["f"] = [f if f["n"] != "supplier_name" else _field("supplier_name", "Ateller")
                           for f in second["r"][0]["f"]]
    env.model((first, 50, "stop"), (second, 50, "stop"))
    code, out = env.run("extract", str(env.image), "--type", "invoice", "--output", str(env.tmp / "o"))
    doc = _doc(out)
    assert "reread_disagreement" in doc["issue_codes"]
    document = Store(env.state).get_document(doc["document_id"])
    supplier = next(o for o in document.observations if o.field == "supplier_name")
    assert supplier.state == "ambiguous"
    code, approved = _approve(env, doc["document_id"], 1, [{"observation_id": supplier.id, "value": "Atelier",
                                                             "raw": "Atelier", "reason": "read on the page"}])
    print("REREAD_APPROVE", code, approved.get("error", {}).get("message"))
    assert code == 0 and approved["approval"] == "human_approved", approved.get("error")


def test_low_contrast_capture_can_be_approved_after_review(env):
    faint = env.tmp / "in" / "faint.png"
    picture = Image.new("RGB", (600, 400), (250, 250, 250))
    ImageDraw.Draw(picture).text((20, 20), "Facture F-1 Total 10,00 EUR", fill=(235, 235, 235))
    picture.save(faint)
    env.model((invoice_reply(), 50, "stop"))
    code, out = env.run("extract", str(faint), "--type", "invoice", "--output", str(env.tmp / "o"))
    doc = _doc(out)
    notes = ["FAINT", code, doc["validation"], doc["issue_codes"]]
    code, approved = _approve(env, doc["document_id"], 1)
    notes += ["FAINT_APPROVE", code, approved.get("error", {}).get("message")]
    code2, again = env.run("extract", str(faint), "--type", "invoice", "--output", str(env.tmp / "o"))
    notes += ["FAINT_REPROCESS", _doc(again)["action"], _doc(again)["version"]]
    print(*notes)
    assert code == 0, "no correction can retire the low_contrast blocker and reprocessing reuses the same version"


# --- F5: correcting an unreadable/disputed field (raw is null) crashes --------------------------------
def test_unreadable_field_can_be_corrected(env):
    reply = invoice_reply()
    reply["r"][0]["f"] = [f if f["n"] != "total" else {**f, "v": None} for f in reply["r"][0]["f"]]
    env.model((reply, 50, "stop"), (reply, 50, "stop"))
    code, out = env.run("extract", str(env.image), "--type", "invoice", "--output", str(env.tmp / "o"))
    document = Store(env.state).get_document(_doc(out)["document_id"])
    total = next(o for o in document.observations if o.field == "total")
    assert total.state == "unreadable" and total.raw is None
    path = env.tmp / "fix.json"
    path.write_text(json.dumps({"document_id": document.document_id, "base_version": 1, "reviewer": "r",
                                "changes": [{"observation_id": total.id, "value": "10.00", "raw": "10,00",
                                             "reason": "read on the page"}]}))
    code, applied = env.run("review", "apply", str(path), "--document", document.document_id)
    assert code == 0, applied.get("error")


def test_reread_disagreement_blocker_retires_once_the_field_is_corrected(env):
    """Bypasses F5 by giving the disputed observation a raw text, then asks the real domain to approve."""
    from vision_tables import domain
    from vision_tables.config import ProcessingError
    from vision_tables.contracts import CorrectionSet, Document

    first = invoice_reply()
    first["r"][0]["f"] = [f if f["n"] != "total" else {**f, "v": None} for f in first["r"][0]["f"]]
    second = invoice_reply()
    second["r"][0]["f"] = [f if f["n"] != "supplier_name" else _field("supplier_name", "Ateller")
                           for f in second["r"][0]["f"]]
    env.model((first, 50, "stop"), (second, 50, "stop"))
    code, out = env.run("extract", str(env.image), "--type", "invoice", "--output", str(env.tmp / "o"))
    document = Store(env.state).get_document(_doc(out)["document_id"])
    data = document.model_dump(mode="json")
    for obs in data["observations"]:
        if obs["field"] == "supplier_name":
            obs["raw"] = "Atelier / Ateller"
            target = obs["id"]
    patched = Document.model_validate(data)
    changes = CorrectionSet.model_validate({"document_id": document.document_id, "base_version": 1, "reviewer": "r",
                                            "changes": [{"observation_id": target, "value": "Atelier", "raw": "Atelier",
                                                         "reason": "read on the page"}], "approve": True})
    try:
        result = domain.apply_corrections(patched, changes)
    except ProcessingError as exc:
        pytest.fail(f"{exc.code}: {exc}")
    assert result.approval == "human_approved"
