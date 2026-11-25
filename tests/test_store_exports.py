import csv
import hashlib
import io
import json
import os
import sqlite3
from pathlib import Path

import pytest

from test_store_support import PNG, build_document, correction, fake_apply
from vision_tables import exports
from vision_tables.config import Settings
from vision_tables.contracts import CorrectionSet
from vision_tables.store import Store


@pytest.fixture
def env(tmp_path):
    settings = Settings(state_dir=tmp_path / "state")
    store = Store(settings.state_dir)
    return store, settings, tmp_path


def persist(env, **build):
    store, settings, tmp = env
    job = store.create_job(kind="extract", input_path="x", output_dir=str(tmp / "out"), settings_json="{}")
    # materialize the artifacts the review bundle must resolve
    root = store.artifact_root(job.job_id)
    (root / "source").mkdir(parents=True)
    (root / "pages").mkdir()
    (root / "source/original.png").write_bytes(build.get("source_bytes", PNG))
    (root / "pages/0001.png").write_bytes(PNG)
    doc = build_document(job.job_id, settings, **build)
    return store.persist_candidates(job.job_id, job.token, [doc])[0]


def read_csv(path):
    return list(csv.DictReader(io.StringIO(Path(path).read_text(encoding="utf-8"))))


@pytest.mark.parametrize("cell, expected", [
    ("=HYPERLINK(\"http://x\")", "'=HYPERLINK(\"http://x\")"), ("+cmd|' /C calc'!A0", "'+cmd|' /C calc'!A0"),
    ("@SUM(1)", "'@SUM(1)"), ("-2+3", "'-2+3"), ("\t=1", "'\t=1"), ("  =1+1", "'  =1+1"), ("-abc", "'-abc"),
    ("-12.50", "-12.50"), ("+33 1 23 45", "+33 1 23 45"), ("1 234,50", "1 234,50"), ("Café", "Café"),
    ("", ""), (None, ""), (True, "true"), (12, "12"),
])
def test_csv_cells_are_safe_but_numbers_and_text_survive(cell, expected):
    assert exports.csv_cell(cell) == expected


def test_csv_escapes_formula_cells_but_canonical_json_keeps_original(env):
    store, _, tmp = env
    got = persist(env, supplier="=cmd|' /C calc'!A0", rows=(("@evil", "-2.00"), ("-1+1", "4.00")), printed_total="2.00")
    info = exports.publish(store, got.document_id, got.version, tmp / "out")
    target = Path(info["directory"])
    rows = {r["observation_id"]: r for r in read_csv(target / "tables/observations.csv")}
    assert rows["o-sup"]["value"].startswith("'=cmd") and rows["o-d0"]["raw"] == "'@evil"
    assert rows["o-p0"]["value"] == "-2.00"  # a credit-note style amount is not damaged
    canon = json.loads((target / "document.json").read_text())
    values = {o["id"]: o["value"] for o in canon["observations"]}
    assert values["o-sup"] == "=cmd|' /C calc'!A0" and values["o-d1"] == "-1+1"
    with store.read() as conn:  # SQLite keeps the original too
        assert conn.execute("SELECT value FROM observations WHERE id='o-sup'").fetchone()[0].startswith("=cmd")


def test_exports_are_deterministic_across_locations_and_share_ids(env):
    store, _, tmp = env
    got = persist(env)
    a = exports.publish(store, got.document_id, 1, tmp / "a")
    b = exports.publish(store, got.document_id, 1, tmp / "b")
    da, db = Path(a["directory"]), Path(b["directory"])
    names = sorted(p.relative_to(da).as_posix() for p in da.rglob("*") if p.is_file() and not p.name.startswith("."))
    assert names == sorted(p.relative_to(db).as_posix() for p in db.rglob("*") if p.is_file()
                           and not p.name.startswith("."))
    for name in names:
        assert (da / name).read_bytes() == (db / name).read_bytes(), name
    assert a["manifest_sha256"] == b["manifest_sha256"]
    manifest = json.loads((da / "manifest.json").read_text())
    assert manifest["snapshot_sha256"] == hashlib.sha256((da / "document.json").read_bytes()).hexdigest()
    for name, sha in manifest["files"].items():
        assert hashlib.sha256((da / name).read_bytes()).hexdigest() == sha
    # relational links: every CSV row joins back to entities/observations by id
    entities = {r["entity_id"] for r in read_csv(da / "tables/entities.csv")}
    obs = read_csv(da / "tables/observations.csv")
    assert {r["entity_id"] for r in obs} <= entities
    assert {r["observation_id"] for r in read_csv(da / "tables/evidence.csv")} <= {r["observation_id"] for r in obs}
    lines = read_csv(da / "tables/table_line_item.csv")
    assert [r["description"] for r in lines] == ["Flour", "Flour"] and lines[0]["entity_id"] != lines[1]["entity_id"]
    assert lines[0]["parent_kind"] == "invoice" and lines[0]["line_total__state"] == "readable"
    calc = {r["observation_id"]: r for r in obs}["o-calc"]
    assert calc["origin"] == "calculated" and json.loads(calc["derivation_inputs"]) == ["o-p0", "o-p1"]
    doc = read_csv(da / "tables/documents.csv")[0]
    assert doc["source_skipped_pages"] == "[]" and doc["approval"] == "needs_review"


def test_json_csv_sqlite_describe_the_same_snapshot(env):
    store, _, tmp = env
    got = persist(env, printed_total="5.00")  # mismatch -> issue + unresolved link
    store.apply_corrections(got.document_id, correction(got.document_id, 1, "o-p0", "2.50"), fake_apply)
    info = exports.publish(store, got.document_id, 2, tmp / "out", exports.FORMATS)
    target = Path(info["directory"])
    canon = json.loads((target / "document.json").read_text())
    csv_obs = {r["observation_id"]: r for r in read_csv(target / "tables/observations.csv")}
    lite = sqlite3.connect(target / "document.sqlite3")
    lite_obs = {r[0]: r for r in lite.execute("SELECT id, value, state, origin FROM observations")}
    with store.read() as conn:
        main_obs = {r[0]: tuple(r) for r in conn.execute(
            "SELECT id, value, state, origin FROM observations WHERE document_id=? AND version=2", (got.document_id,))}
    assert {o["id"] for o in canon["observations"]} == set(csv_obs) == set(lite_obs) == set(main_obs)
    for o in canon["observations"]:
        assert (csv_obs[o["id"]]["value"], csv_obs[o["id"]]["origin"]) == (o["value"], o["origin"])
        assert tuple(lite_obs[o["id"]]) == main_obs[o["id"]]
    assert canon["version"] == 2 and canon["parent_version"] == 1 and canon["corrections"][0]["reviewer"] == "Reviewer A"
    assert [r["code"] for r in read_csv(target / "tables/issues.csv")] == ["total_mismatch"]
    assert read_csv(target / "tables/corrections.csv")[0]["observation_id"] == "o-p0"


def test_interrupted_publication_is_pending_then_recoverable(env):
    store, _, tmp = env
    got = persist(env)
    store.register_export(got.document_id, 1, str(tmp / "out"), ("json", "csv", "review"))

    def crash(name):
        if name == "export:before_manifest":
            raise OSError("power loss")
    Store.fault_hook = staticmethod(crash)
    try:
        with pytest.raises(OSError):
            exports.publish(store, got.document_id, 1, tmp / "out")
    finally:
        Store.fault_hook = None
    target = tmp / "out" / got.document_id / "v0001"
    assert not (target / "manifest.json").exists()  # readers can tell the version is unfinished
    assert [r["state"] for r in store.pending_exports()] == ["pending"]
    (target / ".stage-leftover").mkdir()  # a crashed staging directory is swept on retry
    done = exports.publish_pending(store)
    assert [d["state"] for d in done] == ["published"] and (target / "manifest.json").is_file()
    assert not (target / ".stage-leftover").exists() and store.pending_exports() == []


def test_republish_replaces_a_damaged_unit_atomically(env):
    store, _, tmp = env
    got = persist(env)
    info = exports.publish(store, got.document_id, 1, tmp / "o")
    table = Path(info["directory"]) / "tables" / "entities.csv"
    good = table.read_bytes()
    table.write_bytes(b"corrupt")
    exports.publish(store, got.document_id, 1, tmp / "o")
    assert table.read_bytes() == good


def test_review_bundle_resolves_references_without_modifying_the_source(env):
    store, _, tmp = env
    got = persist(env, printed_total="5.00")
    source = store.artifact_root(got.document.run.job_id) / "source/original.png"
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    info = exports.publish(store, got.document_id, 1, tmp / "fresh-output-dir", ("review",))
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before
    bundle = json.loads((Path(info["directory"]) / "review/review.json").read_text())
    assert bundle["source"]["exists"] and bundle["source"]["sha256_verified"] is True
    assert Path(bundle["source"]["path"]) == source.resolve()
    issue = bundle["issues"][0]
    assert issue["code"] == "total_mismatch" and {o["observation_id"] for o in issue["observations"]} == {"o-total", "o-calc"}
    ev = issue["observations"][0]["evidence"][0]
    assert ev["page_reference"]["image"]["exists"] and ev["region"]["box"]["xyxy"] == [0, 0, 50, 50]
    assert ev["page_reference"]["image_to_original"] == [1, 0, 0, 1, 0, 0]
    assert bundle["history"][0]["version"] == 1 and bundle["possible_duplicates"] == []
    # documented corrections format: schema + an example that parses and an apply command
    example = json.loads((Path(info["directory"]) / "review/corrections.example.json").read_text())
    CorrectionSet.model_validate(example)
    assert bundle["corrections"]["apply"].endswith(got.document_id)
    schema = json.loads((Path(info["directory"]) / "review/corrections.schema.json").read_text())
    assert "base_version" in schema["properties"]


def test_review_bundle_reports_missing_and_escaping_paths_instead_of_following_them(env):
    store, _, tmp = env
    got = persist(env, printed_total="5.00")
    root = store.artifact_root(got.document.run.job_id)
    (root / "pages/0001.png").unlink()
    assert store.resolve_artifact(got.document.run.job_id, "../../../etc/passwd") is None
    info = exports.publish(store, got.document_id, 1, tmp / "o", ("review",))
    bundle = json.loads((Path(info["directory"]) / "review/review.json").read_text())
    image = bundle["issues"][0]["observations"][0]["evidence"][0]["page_reference"]["image"]
    assert image["exists"] is False and image["relative_path"] == "pages/0001.png"
    assert bundle["source"]["exists"] is True


def test_incomplete_documents_show_skipped_source_pages_in_every_format(env):
    store, _, tmp = env
    got = persist(env, incomplete=True)
    info = exports.publish(store, got.document_id, 1, tmp / "o")
    target = Path(info["directory"])
    row = read_csv(target / "tables/documents.csv")[0]
    assert (row["validation"], row["skipped_pages"], row["source_skipped_pages"], row["scope"]) == (
        "incomplete", "[2]", "[2]", "[1,2]")
    bundle = json.loads((target / "review/review.json").read_text())
    assert bundle["coverage"]["source_skipped_pages"] == [2] and bundle["issues"][0]["code"] == "page_skipped"


def test_unknown_format_is_rejected(env):
    store, _, tmp = env
    got = persist(env)
    with pytest.raises(Exception, match="Unknown export format"):
        exports.publish(store, got.document_id, 1, tmp / "o", ("xlsx",))
    assert os.listdir(tmp) == ["state"]  # nothing created for a rejected request


def test_reclassification_lists_latest_and_preserves_exported_type_per_version(env):
    from vision_tables.domain import assemble
    from vision_tables.domain.examples import EXAMPLES, example_context

    store, settings, tmp = env
    ingested, run = example_context()
    for n, family in enumerate(("invoice", "menu"), 1):
        job = store.create_job(kind="extract", input_path="example", output_dir=str(tmp), settings_json="{}")
        current = run.model_copy(update={"job_id": job.job_id, "config_hash": str(n) * 64})
        doc = assemble(ingested, [EXAMPLES[family]()], [1], current, "same-doc")
        store.persist_candidates(job.job_id, job.token, [doc])
    assert store.list_documents()[0]["doc_type"] == "menu"
    for n, family in enumerate(("invoice", "menu"), 1):
        result = Path(exports.publish(store, "same-doc", n, tmp, exports.FORMATS)["directory"])
        assert read_csv(result / "tables/documents.csv")[0]["doc_type"] == family
        assert json.loads((result / "document.json").read_bytes())["doc_type"] == family
        with sqlite3.connect(result / "document.sqlite3") as db:
            assert db.execute("SELECT doc_type FROM documents").fetchone()[0] == family
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
