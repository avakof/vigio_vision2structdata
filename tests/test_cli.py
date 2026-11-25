import json
from pathlib import Path

import pytest

from test_store_support import PNG, build_document, fake_apply, fake_extract
from vision_tables import cli
from vision_tables.config import ProcessingError
from vision_tables.store import Store


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("vision_tables.store.default_apply", lambda: fake_apply)
    monkeypatch.delenv("VT_STATE_DIR", raising=False)

    def invoke(*argv, state=True):
        args = [*argv, *(["--state-dir", str(tmp_path / "state")] if state else []), "--no-progress"]
        code = cli.main(args)
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["exit_code"] == code
        return code, payload
    invoke.tmp = tmp_path
    return invoke


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "in" / "invoice.png"
    path.parent.mkdir()
    path.write_bytes(PNG)
    return path


def use(monkeypatch, extract):
    monkeypatch.setattr("vision_tables.pipeline.extract", extract)
    return extract


def test_extract_reports_ids_versions_status_and_export_paths(run, image, monkeypatch):
    use(monkeypatch, fake_extract())
    code, out = run("extract", str(image), "--output", str(run.tmp / "out"))
    assert code == 0 and out["status"] == "completed" and out["command"] == "extract"
    doc = out["documents"][0]
    assert (doc["version"], doc["action"], doc["review_required"], doc["approval"]) == (1, "created", True, "needs_review")
    assert out["job"]["artifact_root"].endswith(out["job"]["job_id"])
    target = Path(doc["export"]["directory"])
    assert target == run.tmp / "out" / doc["document_id"] / "v0001" or target.resolve() == (
        run.tmp / "out" / doc["document_id"] / "v0001").resolve()
    assert {"document.json", "tables/observations.csv", "review/review.json"} <= set(doc["export"]["files"])
    assert doc["coverage"]["source_skipped_pages"] == [] and doc["possible_duplicates"] == []
    code, shown = run("jobs", "show", out["job"]["job_id"])
    assert shown["job"]["state"] == "completed" and shown["pipeline_status"]["job_id"] == out["job"]["job_id"]


def test_state_dir_comes_from_flag_before_or_after_command_or_environment(run, image, monkeypatch, tmp_path):
    use(monkeypatch, fake_extract())
    other = tmp_path / "elsewhere"
    assert cli.main(["--state-dir", str(other), "--no-progress", "extract", str(image), "--output",
                     str(tmp_path / "o")]) == 0
    monkeypatch.setenv("VT_STATE_DIR", str(tmp_path / "from-env"))
    assert cli.main(["extract", str(image), "--output", str(tmp_path / "o"), "--no-progress"]) == 0
    assert (other / "vision-tables.sqlite3").is_file() and (tmp_path / "from-env/vision-tables.sqlite3").is_file()
    assert not (tmp_path / "state").exists()


def test_page_groups_and_budgets_reach_settings(run, image, monkeypatch):
    seen = {}
    inner = fake_extract()

    def spy(path, output, settings, *, job_id=None):
        seen["settings"] = settings
        return inner(path, output, settings, job_id=job_id)
    use(monkeypatch, spy)
    code, _ = run("extract", str(image), "--output", str(run.tmp / "o"), "--page-group", "1,2", "--page-group", "4-5",
                  "--pipeline", "direct", "--type", "invoice", "--max-pages", "3", "--max-tokens", "512")
    s = seen["settings"]
    assert code == 0 and s.page_groups == ((1, 2), (4, 5)) and s.max_pages == 3 and s.doc_type == "invoice"
    assert s.state_dir == (run.tmp / "state").resolve() or s.state_dir == run.tmp / "state"


@pytest.mark.parametrize("flags", [["--page-group", "2,1"], ["--page-group", "1,2", "--page-group", "2"],
                                   ["--page-group", "x"], ["--page-group", "1-999999999999"],
                                   ["--page-group", "1-900,901-1100"], ["--page-group", "5-2"], ["--max-pages", "0"], ["--pipeline", "magic"]])
def test_bad_settings_are_usage_errors_with_nothing_started(run, image, flags, capsys):
    with pytest.raises(SystemExit) as exc:
        code, out = run("extract", str(image), "--output", str(run.tmp / "o"), *flags)
        raise SystemExit(code)
    assert exc.value.code == 2
    assert not (run.tmp / "state" / "jobs").exists()


def test_failures_incomplete_and_missing_models_have_distinct_nonzero_exits(run, image, monkeypatch):
    use(monkeypatch, fake_extract(incomplete=True))
    code, out = run("extract", str(image), "--output", str(run.tmp / "o"))
    assert (code, out["status"], out["documents"][0]["validation"]) == (3, "incomplete", "incomplete")
    assert out["documents"][0]["coverage"]["source_skipped_pages"] == [2]

    def no_data(path, output, settings, *, job_id=None):
        (settings.state_dir / "jobs" / job_id).mkdir(parents=True)
        raise ProcessingError("no_extraction", "No page yielded fields.")
    use(monkeypatch, no_data)
    code, out = run("extract", str(image), "--output", str(run.tmp / "o2"))
    assert (code, out["status"], out["error"]["code"]) == (4, "failed", "no_extraction")
    assert out["error"]["artifact_root"].endswith(out["job"]["job_id"]) and out["error"]["next"]
    with Store(run.tmp / "state").read() as conn:  # failure reserved no document or extraction key
        assert conn.execute("SELECT COUNT(*) FROM extractions").fetchone()[0] == 1
        assert conn.execute("SELECT state, error_code FROM jobs WHERE job_id=?", (out["job"]["job_id"],)).fetchone()[:] == (
            "failed", "no_extraction")

    def missing(path, output, settings, *, job_id=None):
        raise ProcessingError("missing_model", "Run vision-tables doctor.")
    use(monkeypatch, missing)
    code, out = run("extract", str(image), "--output", str(run.tmp / "o3"))
    assert (code, out["error"]["code"]) == (5, "missing_model") and "models prepare" in out["error"]["next"]

    def defect(path, output, settings, *, job_id=None):
        raise RuntimeError("boom")
    use(monkeypatch, defect)
    code, out = run("extract", str(image), "--output", str(run.tmp / "o4"))
    assert (code, out["error"]["code"]) == (1, "internal_error")
    code, out = run("extract", str(run.tmp / "nope.png"), "--output", str(run.tmp / "o5"))
    assert (code, out["error"]["code"]) == (7, "invalid_input")


def test_doctor_without_models_exits_nonzero_and_says_what_is_missing(run):
    code, out = run("doctor")
    assert code == 5 and out["status"] == "not_ready" and out["next"] and out["hardware"]["machine"]


def test_reimport_changed_config_and_possible_duplicate(run, image, monkeypatch):
    use(monkeypatch, fake_extract())
    out_dir = str(run.tmp / "out")
    _, first = run("extract", str(image), "--output", out_dir)
    _, again = run("extract", str(image), "--output", str(run.tmp / "out2"))
    a, b = first["documents"][0], again["documents"][0]
    assert (b["action"], b["version"], b["document_id"]) == ("reused", 1, a["document_id"])
    _, changed = run("extract", str(image), "--output", out_dir, "--max-tokens", "512")
    c = changed["documents"][0]
    assert (c["action"], c["version"], c["parent_version"], c["base_version"]) == ("new_extraction", 2, 1, 2)
    use(monkeypatch, fake_extract(source_bytes=PNG + b"another photo"))
    _, twin = run("extract", str(image), "--output", out_dir)
    assert twin["documents"][0]["possible_duplicates"][0]["document_id"] == a["document_id"]
    _, docs = run("documents", "list")
    assert len(docs["documents"]) == 2
    _, shown = run("documents", "show", a["document_id"])
    assert [v["version"] for v in shown["versions"]] == [1, 2] and shown["possible_duplicates"]


def write(tmp, name, data):
    path = tmp / name
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def corrections(doc_id, base, obs="o-p0", value="2.50", approve=False):
    return {"document_id": doc_id, "base_version": base, "reviewer": "R. Chef", "approve": approve,
            "changes": [{"observation_id": obs, "value": value, "raw": value, "reason": "read the page"}] if obs else []}


def test_review_apply_versions_replays_conflicts_and_rejects_bad_files(run, image, monkeypatch):
    use(monkeypatch, fake_extract(printed_total="5.00"))
    out_dir = str(run.tmp / "out")
    _, first = run("extract", str(image), "--output", out_dir)
    did = first["documents"][0]["document_id"]
    good = write(run.tmp, "c.json", corrections(did, 1))
    code, applied = run("review", "apply", str(good), "--document", did, "--output", out_dir)
    assert code == 0 and (applied["status"], applied["version"], applied["base_version"]) == ("applied", 2, 1)
    assert applied["issue_codes"] == ["total_mismatch"] and applied["review_required"] is True
    v2 = Path(out_dir) / did / "v0002"
    assert (v2 / "manifest.json").is_file() and (Path(out_dir) / did / "v0001" / "manifest.json").is_file()
    assert json.loads((v2 / "document.json").read_text())["corrections"][0]["reviewer"] == "R. Chef"
    # repeat: idempotent answer, same version, no new files
    code, replay = run("review", "apply", str(good), "--document", did, "--output", out_dir)
    assert (code, replay["status"], replay["version"]) == (0, "replayed", 2)
    # different edit on the stale base, wrong document, duplicate keys, oversize types: nothing is applied
    code, stale = run("review", "apply", str(write(run.tmp, "s.json", corrections(did, 1, "o-p1", "9.00"))),
                      "--document", did)
    assert (code, stale["error"]["code"]) == (6, "stale_base")
    code, wrong = run("review", "apply", str(good), "--document", "doc-other")
    assert (code, wrong["error"]["code"]) == (7, "document_mismatch")
    dup = '{"document_id": "%s", "base_version": 2, "reviewer": "r", "approve": true, "approve": false, "changes": []}' % did
    code, bad = run("review", "apply", str(write(run.tmp, "d.json", dup)), "--document", did)
    assert code == 7 and "duplicate JSON key" in bad["error"]["message"]
    boolean = corrections(did, 2, "o-p1", True)
    code, bad = run("review", "apply", str(write(run.tmp, "b.json", boolean)), "--document", did)
    assert code == 7 and bad["error"]["code"] == "invalid_input"
    code, bad = run("review", "apply", str(write(run.tmp, "e.json", corrections(did, 2, None))), "--document", did)
    assert code == 7  # neither changes nor approval
    code, bad = run("review", "apply", str(write(run.tmp, "n.json", corrections(did, 2, "o-calc", "1.00"))),
                    "--document", did)
    assert (code, bad["error"]["code"]) == (7, "invalid_correction")
    _, shown = run("documents", "show", did)
    assert shown["latest_version"] == 2


def test_review_apply_regenerates_every_known_export_location(run, image, monkeypatch):
    use(monkeypatch, fake_extract())
    _, first = run("extract", str(image), "--output", str(run.tmp / "outA"))
    did = first["documents"][0]["document_id"]
    run("export", "--document", did, "--format", "csv", "--output", str(run.tmp / "outB"))
    code, applied = run("review", "apply", str(write(run.tmp, "c.json", corrections(did, 1))), "--document", did,
                        "--output", str(run.tmp / "outC"))
    dirs = sorted(Path(e["directory"]).parents[1].name for e in applied["exports"])
    assert code == 0 and dirs == ["outA", "outB", "outC"]


def test_approval_needs_explicit_decision_and_blocks_on_open_issues(run, image, monkeypatch):
    use(monkeypatch, fake_extract(printed_total="5.00"))
    _, first = run("extract", str(image), "--output", str(run.tmp / "o"))
    did = first["documents"][0]["document_id"]
    code, blocked = run("review", "apply", str(write(run.tmp, "a.json", corrections(did, 1, None, approve=True))),
                        "--document", did)
    assert (code, blocked["error"]["code"]) == (7, "invalid_correction")
    use(monkeypatch, fake_extract())
    _, clean = run("extract", str(image), "--output", str(run.tmp / "o"), "--max-tokens", "300")
    clean_id, version = clean["documents"][0]["document_id"], clean["documents"][0]["version"]
    assert clean["documents"][0]["approval"] == "needs_review"
    code, ok = run("review", "apply", str(write(run.tmp, "a2.json", corrections(clean_id, version, None, approve=True))),
                   "--document", clean_id)
    assert (code, ok["approval"], ok["review_required"]) == (0, "human_approved", False)


def test_export_formats_selection_and_errors(run, image, monkeypatch):
    use(monkeypatch, fake_extract())
    _, first = run("extract", str(image), "--output", str(run.tmp / "o"))
    did = first["documents"][0]["document_id"]
    code, out = run("export", "--document", did, "--format", "all", "--output", str(run.tmp / "x"))
    target = Path(out["exports"][0]["directory"])
    assert code == 0 and (target / "document.sqlite3").is_file() and (target / "tables/entities.csv").is_file()
    code, out = run("export", "--document", did, "--format", "json", "--version", "1", "--output", str(run.tmp / "y"))
    assert code == 0 and not (Path(out["exports"][0]["directory"]) / "tables").exists()
    code, out = run("export", "--document", did, "--format", "xlsx")
    assert (code, out["error"]["code"]) == (7, "invalid_format")
    code, out = run("export", "--document", "doc-missing", "--format", "json")
    assert (code, out["error"]["code"]) == (7, "unknown_document")
    code, out = run("export", "--document", did, "--format", "json", "--version", "9")
    assert (code, out["error"]["code"]) == (7, "unknown_version")
    code, out = run("review", "bundle", "--document", did, "--output", str(run.tmp / "bundle"))
    assert code == 0 and (Path(out["exports"][0]["directory"]) / "review/review.json").is_file()


def test_batch_reports_per_document_status_and_resumes_without_rework(run, monkeypatch):
    inbox = run.tmp / "inbox"
    inbox.mkdir()
    for name, data in (("a.png", PNG + b"a"), ("b.png", PNG + b"bad"), ("c.pdf", PNG + b"c"), ("notes.txt", b"hi"),
                       (".hidden.png", b"x")):
        (inbox / name).write_bytes(data)
    calls, state = [], {"fail": True, "cancel_on": None}

    def extract(path, output, settings, *, job_id=None):
        calls.append(path.name)
        if path.name == "b.png" and state["fail"]:
            (settings.state_dir / "jobs" / job_id).mkdir(parents=True)
            raise ProcessingError("no_extraction", "unreadable photo")
        if path.name == state["cancel_on"]:
            raise KeyboardInterrupt
        return fake_extract(source_bytes=path.read_bytes())(path, output, settings, job_id=job_id)
    use(monkeypatch, extract)
    code, first = run("batch", str(inbox), "--output", str(run.tmp / "out"))
    states = {i["name"]: i["state"] for i in first["items"]}
    assert code == 4 and states == {"a.png": "completed", "b.png": "failed", "c.pdf": "completed",
                                    "notes.txt": "skipped"} and calls == ["a.png", "b.png", "c.pdf"]
    item = next(i for i in first["items"] if i["name"] == "b.png")
    assert item["error"]["code"] == "no_extraction" and item["job"]["artifact_root"]
    assert Path(first["status_file"]).is_file()
    # resume: completed work is not repeated, the failure is not retried unless asked
    code, second = run("batch", str(inbox), "--output", str(run.tmp / "out"))
    assert code == 4 and calls == ["a.png", "b.png", "c.pdf"] and second["batch_id"] == first["batch_id"]
    state["fail"] = False
    code, third = run("batch", str(inbox), "--output", str(run.tmp / "out"), "--retry-failed")
    assert code == 0 and calls[3:] == ["b.png"] and third["counts"] == {"completed": 3, "skipped": 1}
    # a changed file is processed again
    (inbox / "a.png").write_bytes(PNG + b"a edited")
    code, fourth = run("batch", str(inbox), "--output", str(run.tmp / "out"))
    assert code == 0 and calls[4:] == ["a.png"]


def test_batch_cancellation_is_recoverable(run, monkeypatch):
    inbox = run.tmp / "inbox"
    inbox.mkdir()
    for name in ("1.png", "2.png", "3.png"):
        (inbox / name).write_bytes(PNG + name.encode())
    calls, state = [], {"cancel_on": "2.png"}

    def extract(path, output, settings, *, job_id=None):
        calls.append(path.name)
        if path.name == state["cancel_on"]:
            (settings.state_dir / "jobs" / job_id).mkdir(parents=True)
            raise KeyboardInterrupt
        return fake_extract(source_bytes=path.read_bytes())(path, output, settings, job_id=job_id)
    use(monkeypatch, extract)
    code, first = run("batch", str(inbox), "--output", str(run.tmp / "out"))
    assert code == 130 and first["status"] == "cancelled"
    assert {i["name"]: i["state"] for i in first["items"]} == {"1.png": "completed", "2.png": "cancelled", "3.png": "pending"}
    _, jobs = run("jobs", "list", "--state", "cancelled")
    assert len(jobs["jobs"]) == 1
    state["cancel_on"] = None
    code, second = run("batch", str(inbox), "--output", str(run.tmp / "out"))
    assert code == 0 and calls == ["1.png", "2.png", "2.png", "3.png"]


def test_jobs_recover_finishes_staged_result_without_new_inference(run, image, monkeypatch):
    use(monkeypatch, fake_extract())
    Store.fault_hook = staticmethod(lambda name: (_ for _ in ()).throw(KeyboardInterrupt)
                                    if name == "persist:before_commit" else None)
    try:
        code, cancelled = run("extract", str(image), "--output", str(run.tmp / "out"))
    finally:
        Store.fault_hook = None
    assert code == 130 and cancelled["status"] == "cancelled" and "jobs recover" in cancelled["error"]["next"]
    assert not list((run.tmp / "state").glob("jobs/*/documents.json")) == []
    _, docs = run("documents", "list")
    assert docs["documents"] == []  # nothing half-committed

    def forbidden(*a, **k):
        raise AssertionError("recovery must not run inference")
    use(monkeypatch, forbidden)
    code, rec = run("jobs", "recover")
    assert code == 0 and rec["jobs"][0]["action"] == "recovered" and rec["jobs"][0]["documents"][0]["version"] == 1
    assert Path(rec["jobs"][0]["documents"][0]["export"]["directory"], "manifest.json").is_file()
    code, again = run("jobs", "recover")
    assert code == 0 and again["jobs"] == []


def test_evaluate_runs_the_manifest_with_declared_configuration(run, monkeypatch):
    manifest = Path(__file__).resolve().parents[1] / "fixtures" / "manifest.json"
    seen = []

    def unavailable(path, output, settings, *, job_id=None):
        seen.append(settings.pipeline)
        raise ProcessingError("missing_model", "no local model in this test")
    use(monkeypatch, unavailable)
    code, out = run("evaluate", str(manifest), "--output", str(run.tmp / "eval"), "--pipeline", "direct",
                    "--max-tokens", "256")
    report = json.loads(Path(out["report"]).read_text())
    assert code == 0 and out["cases"] == len(report["case_results"]) == len(seen) > 0
    assert report["configuration"]["max_tokens"] == 256 and out["manifest_sha256"] == report["manifest_sha256"]
    assert all(c["status"] == "missing_model" for c in report["case_results"])  # abstention is not credited


def _inbox(tmp, **files):
    inbox = tmp / "inbox"
    inbox.mkdir(exist_ok=True)
    for name, data in files.items():
        (inbox / name.replace("_", ".")).write_bytes(data)
    return inbox


def test_batch_resumes_only_under_the_same_effective_version(run, monkeypatch):
    from vision_tables import models
    inbox = _inbox(run.tmp, a_png=PNG + b"a", b_png=PNG + b"b")
    extract = use(monkeypatch, fake_extract())
    out = str(run.tmp / "out")
    code, first = run("batch", str(inbox), "--output", out)
    assert code == 0 and first["counts"] == {"completed": 2} and len(extract.calls) == 2
    code, same = run("batch", str(inbox), "--output", out)  # identical arguments and version: nothing repeated
    assert same["batch_id"] == first["batch_id"] and len(extract.calls) == 2
    real = models.runtime_info

    def upgraded(settings):  # same command line, newer model revision / component
        return {**real(settings), "revision": "upgraded-revision"}
    monkeypatch.setattr(models, "runtime_info", upgraded)
    code, new = run("batch", str(inbox), "--output", out)
    assert code == 0 and new["batch_id"] != first["batch_id"] and new["fingerprint"] != first["fingerprint"]
    assert len(extract.calls) == 4 and new["counts"] == {"completed": 2}
    monkeypatch.setattr(models, "runtime_info", real)
    code, back = run("batch", str(inbox), "--output", out)  # the old version's batch is still resumable
    assert back["batch_id"] == first["batch_id"] and len(extract.calls) == 4


def test_batch_fingerprint_is_the_extraction_fingerprint_and_computed_once(run, monkeypatch):
    from vision_tables import pipeline
    inbox = _inbox(run.tmp, a_png=PNG + b"a", b_png=PNG + b"b", c_png=PNG + b"c")
    use(monkeypatch, fake_extract())
    seen = []
    real = pipeline.extraction_fingerprint
    monkeypatch.setattr(pipeline, "extraction_fingerprint", lambda s, i: seen.append(1) or real(s, i))
    code, out = run("batch", str(inbox), "--output", str(run.tmp / "out"))
    assert code == 0 and len(seen) == 1 and len(out["fingerprint"]) == 64


def test_oversized_and_unreadable_files_are_not_read_and_fail_visibly(run, monkeypatch):
    import vision_tables.store_jobs as jobs
    inbox = _inbox(run.tmp, ok_png=PNG + b"ok", big_png=b"x" * 5000)
    (inbox / "link.png").symlink_to(inbox / "ok.png")
    (inbox / "folder.png").mkdir()
    extract = use(monkeypatch, fake_extract())
    code, out = run("batch", str(inbox), "--output", str(run.tmp / "out"), "--max-bytes", "1000")
    states = {i["name"]: i["state"] for i in out["items"]}
    assert code == 4 and states == {"big.png": "failed", "ok.png": "completed"}
    big = next(i for i in out["items"] if i["name"] == "big.png")
    assert big["error"]["code"] == "source_too_large" and "--max-bytes" in big["error"]["message"]
    assert [p.name for p in extract.calls] == ["ok.png"] and "was not read" in big["error"]["message"]
    with pytest.raises(ProcessingError) as exc:  # growth after the size check is also bounded
        jobs.file_sha256(inbox / "big.png", 1000)
    assert exc.value.code == "source_too_large"
    with pytest.raises(ProcessingError) as exc:
        jobs.file_sha256(inbox / "link.png", 1000)
    assert exc.value.code == "invalid_input"
    monkeypatch.undo()
    # a smaller replacement is accepted on the next run without --retry-failed
    (inbox / "big.png").write_bytes(PNG + b"small")
    use(monkeypatch, fake_extract())
    monkeypatch.setattr("vision_tables.store.default_apply", lambda: fake_apply)
    code, again = run("batch", str(inbox), "--output", str(run.tmp / "out"), "--max-bytes", "1000")
    assert code == 0 and {i["name"]: i["state"] for i in again["items"]} == {"big.png": "completed",
                                                                            "ok.png": "completed"}


def test_batch_with_only_unsupported_files_is_a_visible_failure(run, monkeypatch):
    inbox = _inbox(run.tmp, notes_txt=b"hi", data_csv=b"a,b")
    extract = use(monkeypatch, fake_extract())
    code, out = run("batch", str(inbox), "--output", str(run.tmp / "out"))
    assert (code, out["status"], out["error"]["code"]) == (7, "failed", "no_supported_files")
    assert out["counts"] == {"skipped": 2} and not extract.calls and "JPEG" in out["error"]["next"]
    empty = run.tmp / "empty"
    empty.mkdir()
    code, out = run("batch", str(empty), "--output", str(run.tmp / "out2"))
    assert (code, out["error"]["code"]) == (7, "no_supported_files")
