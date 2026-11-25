"""Real-process interruption tests: signals, hard kills at transaction boundaries, and recovery.

The child process is this file run as a script with the pipeline replaced by a deterministic stand-in,
so the CLI, store, signal handling, lease and recovery code run unmodified.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()


def _child(mode: str, argv: list[str]) -> int:
    sys.path.insert(0, str(HERE.parent))
    from test_store_support import fake_apply, fake_extract
    import vision_tables.pipeline
    import vision_tables.store
    from vision_tables import cli
    from vision_tables.store import Store

    inner = fake_extract()
    marker = Path(os.environ["CHILD_MARKER"])

    def extract(path, output, settings, *, job_id=None):
        docs = inner(path, output, settings, job_id=job_id)
        if mode in ("sleep",):
            marker.write_text("started")
            time.sleep(120)
        return docs
    vision_tables.pipeline.extract = extract
    vision_tables.store.default_apply = lambda: fake_apply
    crash_at = {"die_before_commit": "persist:before_commit", "die_after_commit": "persist:after_commit",
                "die_before_manifest": "export:before_manifest", "die_correct": "correct:after_commit"}.get(mode)
    if crash_at:
        Store.fault_hook = staticmethod(lambda name: os._exit(9) if name == crash_at else None)
    return cli.main(argv)


def finish(proc, timeout=60):
    out, err = proc.communicate(timeout=timeout)
    return proc.returncode, out, err


def start(tmp, mode, *argv):
    env = {**os.environ, "CHILD_MARKER": str(tmp / "marker")}
    cmd = [sys.executable, str(HERE), "child", mode, *argv, "--state-dir", str(tmp / "state"), "--no-progress"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "invoice.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nsubprocess page")
    return path


def cli_json(tmp, *argv):
    code, out, err = finish(start(tmp, "normal", *argv))
    return code, json.loads(out)


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_signal_cancels_recoverably_and_a_rerun_completes(tmp_path, image, sig):
    proc = start(tmp_path, "sleep", "extract", str(image), "--output", str(tmp_path / "out"))
    for _ in range(300):
        if (tmp_path / "marker").exists():
            break
        time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("child never reached extraction")
    proc.send_signal(sig)
    code, out, _ = finish(proc)
    payload = json.loads(out)
    assert code == 130 and payload["status"] == "cancelled" and payload["error"]["code"] == "cancelled"
    code, jobs = cli_json(tmp_path, "jobs", "list")
    [job] = jobs["jobs"]
    assert job["state"] == "cancelled" and job["artifact_root"].endswith(job["job_id"])
    assert "Rerun" in payload["error"]["next"]  # interrupted during inference: nothing staged, nothing committed
    assert not (tmp_path / "state" / "jobs" / job["job_id"] / "documents.json").exists()
    code, docs = cli_json(tmp_path, "documents", "list")
    assert docs["documents"] == []
    code, rec = cli_json(tmp_path, "jobs", "recover")
    assert code == 0 and rec["jobs"] == [] and rec["status"] == "ok"
    code, again = cli_json(tmp_path, "extract", str(image), "--output", str(tmp_path / "out"))
    assert code == 0 and again["documents"][0]["action"] == "created" and again["documents"][0]["version"] == 1
    code, jobs = cli_json(tmp_path, "jobs", "list")
    assert sorted(j["state"] for j in jobs["jobs"]) == ["cancelled", "completed"]  # the evidence of the cancelled run stays


def test_hard_kill_before_commit_leaves_nothing_then_recovery_publishes_without_inference(tmp_path, image):
    code, out, _ = finish(start(tmp_path, "die_before_commit", "extract", str(image), "--output", str(tmp_path / "out")))
    assert code == 9 and out == ""
    code, docs = cli_json(tmp_path, "documents", "list")
    assert docs["documents"] == []
    code, jobs = cli_json(tmp_path, "jobs", "list")
    assert [j["state"] for j in jobs["jobs"]] == ["persisting"]  # owner is dead; no lease wait needed
    code, rec = cli_json(tmp_path, "jobs", "recover")
    assert code == 0 and rec["jobs"][0]["action"] == "recovered" and rec["jobs"][0]["documents"][0]["version"] == 1
    manifest = Path(rec["jobs"][0]["documents"][0]["export"]["manifest"])
    assert manifest.is_file()
    code, docs = cli_json(tmp_path, "documents", "list")
    assert len(docs["documents"]) == 1
    code, jobs = cli_json(tmp_path, "jobs", "list", "--state", "completed")
    assert len(jobs["jobs"]) == 1


def test_hard_kill_after_commit_before_export_is_pending_then_recovered_and_reimport_is_consistent(tmp_path, image):
    code, out, _ = finish(start(tmp_path, "die_after_commit", "extract", str(image), "--output", str(tmp_path / "out")))
    assert code == 9
    code, docs = cli_json(tmp_path, "documents", "list")
    [doc] = docs["documents"]
    assert doc["version"] == 1 and not list((tmp_path / "out").glob("*/*/manifest.json"))
    code, rec = cli_json(tmp_path, "jobs", "recover")
    assert code == 0
    [manifest] = (tmp_path / "out").glob("*/v0001/manifest.json")
    assert json.loads(manifest.read_text())["document_id"] == doc["document_id"]
    code, again = cli_json(tmp_path, "extract", str(image), "--output", str(tmp_path / "out2"))
    assert again["documents"][0]["action"] == "reused" and (tmp_path / "out2").exists()
    code, rec2 = cli_json(tmp_path, "jobs", "recover")
    assert rec2["jobs"] == [] and rec2["exports"] == []


def test_hard_kill_mid_publication_then_retry_publishes_one_consistent_version(tmp_path, image):
    code, _, _ = finish(start(tmp_path, "die_before_manifest", "extract", str(image), "--output", str(tmp_path / "out")))
    assert code == 9
    [version_dir] = list((tmp_path / "out").glob("*/v0001"))
    assert (version_dir / "document.json").is_file() and not (version_dir / "manifest.json").exists()
    code, rec = cli_json(tmp_path, "jobs", "recover")
    assert code == 0 and (version_dir / "manifest.json").is_file()
    manifest = json.loads((version_dir / "manifest.json").read_text())
    import hashlib
    for name, sha in manifest["files"].items():
        assert hashlib.sha256((version_dir / name).read_bytes()).hexdigest() == sha


def test_hard_kill_after_correction_commit_is_idempotent_on_retry(tmp_path, image):
    code, first = cli_json(tmp_path, "extract", str(image), "--output", str(tmp_path / "out"))
    did = first["documents"][0]["document_id"]
    fix = tmp_path / "fix.json"
    fix.write_text(json.dumps({"document_id": did, "base_version": 1, "reviewer": "R", "approve": False, "changes": [
        {"observation_id": "o-p0", "value": "2.50", "raw": "2.50", "reason": "checked"}]}))
    code, _, _ = finish(start(tmp_path, "die_correct", "review", "apply", str(fix), "--document", did,
                              "--output", str(tmp_path / "out")))
    assert code == 9
    assert not (tmp_path / "out" / did / "v0002" / "manifest.json").exists()
    code, retry = cli_json(tmp_path, "review", "apply", str(fix), "--document", did, "--output", str(tmp_path / "out"))
    assert code == 0 and (retry["status"], retry["version"]) == ("replayed", 2)
    assert (tmp_path / "out" / did / "v0002" / "manifest.json").is_file()
    code, shown = cli_json(tmp_path, "documents", "show", did)
    assert [v["version"] for v in shown["versions"]] == [1, 2]


if __name__ == "__main__" and len(sys.argv) > 3 and sys.argv[1] == "child":
    sys.exit(_child(sys.argv[2], sys.argv[3:]))
