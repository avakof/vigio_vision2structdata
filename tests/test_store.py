import hashlib
import json
import sqlite3
import threading
from decimal import Decimal

import pytest

from test_store_support import PNG, build_document, correction, fake_apply, raises
from vision_tables import exports
from vision_tables.config import Settings
from vision_tables.migrations import MIGRATIONS, Migration, apply_migrations
from vision_tables.store import Store, signature


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state")


@pytest.fixture
def settings(tmp_path):
    return Settings(state_dir=tmp_path / "state")


def persist(store, settings, **build):
    """One job -> one persisted candidate, like process_file without publication."""
    job = store.create_job(kind="extract", input_path="/in/x.png", output_dir=str(store.state_dir / "out"),
                           settings_json="{}")
    doc = build_document(job.job_id, settings, **build)
    return job, store.persist_candidates(job.job_id, job.token, [doc])[0]


def counts(store):
    with store.read() as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("documents", "document_versions", "extractions", "observations", "evidence_links",
                          "entities", "issues", "pages", "regions")}


# -- migrations -------------------------------------------------------------------------------------
def test_migration_001_is_idempotent_and_checksummed(tmp_path):
    first = Store(tmp_path / "s")
    Store(tmp_path / "s")  # reopen: nothing re-applied
    with first.read() as conn:
        rows = conn.execute("SELECT version, name, checksum FROM schema_migrations").fetchall()
    assert [(r[0], r[1]) for r in rows] == [(1, "initial"), (2, "export_published_formats"),
                                               (3, "review_severity_and_crop_transforms")]
    assert [r[2] for r in rows] == [m.checksum for m in MIGRATIONS]
    conn = sqlite3.connect(first.path, isolation_level=None)
    assert apply_migrations(conn, "now") == []
    tampered = (Migration(1, "initial", MIGRATIONS[0].statements[:-1]), *MIGRATIONS[1:])
    with raises("migration_mismatch"):
        apply_migrations(conn, "now", tampered)
    conn.execute("INSERT INTO schema_migrations VALUES (99,'future','x','now')")
    with raises("database_newer"):
        apply_migrations(conn, "now")


def test_snapshot_rows_are_immutable(store, settings):
    persist(store, settings)
    with store.read() as conn:
        for sql in ("UPDATE document_versions SET approval='human_approved'", "DELETE FROM observations",
                    "UPDATE evidence_links SET quote='x'", "DELETE FROM pages"):
            with pytest.raises(sqlite3.DatabaseError, match="immutable"):
                conn.execute(sql)


def test_parameterized_storage_keeps_hostile_text_inert(store, settings):
    evil = "x'); DROP TABLE documents;--"
    persist(store, settings, supplier=evil)
    assert counts(store)["documents"] == 1
    with store.read() as conn:
        assert conn.execute("SELECT value FROM observations WHERE id='o-sup'").fetchone()[0] == evil


# -- extraction identity, reimport, versions ------------------------------------------------------------
def test_reimport_same_config_reuses_one_base_and_materializes_links(store, settings):
    _, first = persist(store, settings)
    before = counts(store)
    _, again = persist(store, settings)
    assert (first.action, first.version) == ("created", 1)
    assert (again.action, again.version, again.created_version) == ("reused", 1, None)
    after = counts(store)
    assert after["document_versions"] == before["document_versions"] == 1
    assert after["extractions"] == 1 and after["observations"] == before["observations"]
    with store.read() as conn:
        link = conn.execute("SELECT * FROM evidence_links WHERE observation_id='o-total'").fetchone()
        assert (link["source_path"], link["page_image_path"], link["job_id"]) == (
            "source/original.png", "pages/0001.png", first.document.run.job_id)
        assert json.loads(link["region_box_json"])["xyxy"] == [0, 0, 50, 50]
        # identical legitimate rows stay two distinct entities with their own observations
        assert conn.execute("SELECT COUNT(*) FROM entities WHERE kind='line_item'").fetchone()[0] == 2


def test_two_simultaneous_jobs_resolve_to_one_base(store, settings):
    jobs = [store.create_job(kind="extract", input_path="x", output_dir="o", settings_json="{}") for _ in range(2)]
    docs = [build_document(j.job_id, settings) for j in jobs]
    barrier, out = threading.Barrier(2), []

    def run(job, doc):
        barrier.wait()
        out.append(store.persist_candidates(job.job_id, job.token, [doc])[0])
    threads = [threading.Thread(target=run, args=(j, d)) for j, d in zip(jobs, docs)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(r.action for r in out) == ["created", "reused"] and {r.version for r in out} == {1}
    assert counts(store)["document_versions"] == 1


def test_reimport_after_correction_returns_reviewed_descendant(store, settings):
    _, first = persist(store, settings)
    did = first.document_id
    result = store.apply_corrections(did, correction(did, 1, approve=False), fake_apply)
    assert result.version == 2 and result.document.observations[2].origin in ("observed", "human_supplied")
    _, again = persist(store, settings)
    assert (again.version, again.base_version, again.action) == (2, 1, "reused")
    assert again.document.corrections and again.document.corrections[-1].reviewer == "Reviewer A"
    assert [v["version"] for v in store.versions(did)] == [1, 2]


def test_changed_config_creates_traceable_extraction_and_preserves_reviewed_lineage(store, settings):
    _, first = persist(store, settings)
    did = first.document_id
    store.apply_corrections(did, correction(did, 1), fake_apply)  # v2 on lineage 1
    other = settings.model_copy(update={"max_tokens": 4096})
    _, changed = persist(store, other)
    assert (changed.action, changed.version, changed.parent_version, changed.base_version) == ("new_extraction", 3, 2, 3)
    assert changed.document.approval == "needs_review" and not changed.document.corrections
    versions = store.versions(did)
    assert [(v["version"], v["parent_version"], v["kind"], v["base_version"]) for v in versions] == [
        (1, None, "extraction", 1), (2, 1, "correction", 1), (3, 2, "extraction", 3)]
    # the old lineage's head is still version 2 and can still be reviewed; version 1 is stale
    again = store.apply_corrections(did, correction(did, 2, "o-p1", "2.50"), fake_apply)
    assert (again.version, again.base_version) == (4, 2)
    with raises("stale_base"):
        store.apply_corrections(did, correction(did, 1, "o-p1", "9.99"), fake_apply)
    _, reimport = persist(store, settings)
    assert reimport.version == 4  # original config still resolves to its reviewed descendant


def test_incomplete_base_is_provisional_and_a_complete_retry_supersedes_it(store, settings):
    _, first = persist(store, settings, incomplete=True)
    assert first.document.validation == "incomplete"
    _, same = persist(store, settings, incomplete=True)
    assert (same.action, same.version) == ("reused", 1)  # still incomplete: no churn
    complete = build_document("ignored", settings)
    assert complete.document_id != first.document_id  # different scope = different logical document
    # same logical document: the incomplete candidate and a complete one share id and config
    job = store.create_job(kind="extract", input_path="x", output_dir="o", settings_json="{}")
    retry = build_document(job.job_id, settings, incomplete=True).model_dump(mode="json")
    retry.update(validation="issues", coverage={**retry["coverage"], "skipped_pages": [], "source_skipped_pages": [],
                                                 "processed_pages": [1, 2]})
    retry["pages"].append({**retry["pages"][0], "number": 2, "image_path": "pages/0002.png"})
    retry["issues"] = []
    from vision_tables.contracts import Document
    got = store.persist_candidates(job.job_id, job.token, [Document.model_validate(retry)])[0]
    assert (got.action, got.version, got.parent_version) == ("superseded", 2, 1)
    with store.read() as conn:
        assert conn.execute("SELECT version, finality FROM extractions").fetchone()[:] == (2, "final")


def test_edited_incomplete_base_is_not_superseded_by_retry(store, settings):
    _, first = persist(store, settings, incomplete=True)
    store.apply_corrections(first.document_id, correction(first.document_id, 1), fake_apply)
    job = store.create_job(kind="extract", input_path="x", output_dir="o", settings_json="{}")
    data = build_document(job.job_id, settings, incomplete=True).model_dump(mode="json")
    data.update(validation="issues", issues=[], coverage={"processed_pages": [1, 2], "skipped_pages": [],
                                                           "source_skipped_pages": [], "incomplete_pages": []})
    data["pages"].append({**data["pages"][0], "number": 2, "image_path": "pages/0002.png"})
    from vision_tables.contracts import Document
    got = store.persist_candidates(job.job_id, job.token, [Document.model_validate(data)])[0]
    assert (got.action, got.version) == ("reused", 2)  # reviewer work wins


def test_page_group_documents_keep_skipped_members_and_source_skipped_pages(store, settings):
    _, got = persist(store, settings, incomplete=True)
    assert got.document.scope == [1, 2] and got.document.coverage.source_skipped_pages == [2]
    with store.read() as conn:
        assert json.loads(conn.execute("SELECT scope_json FROM documents").fetchone()[0]) == [1, 2]


# -- corrections --------------------------------------------------------------------------------------------
def test_correction_is_replay_safe_before_stale_check_and_conflicts_otherwise(store, settings):
    _, first = persist(store, settings)
    did = first.document_id
    edit = correction(did, 1)
    one = store.apply_corrections(did, edit, fake_apply)
    again = store.apply_corrections(did, edit, fake_apply)  # base 1 is stale now, but this is a replay
    assert (one.version, one.replayed, again.version, again.replayed) == (2, False, 2, True)
    with raises("stale_base"):
        store.apply_corrections(did, correction(did, 1, "o-p1", "7.00"), fake_apply)
    assert counts(store)["document_versions"] == 2
    with store.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0] == 1


def test_correction_preserves_original_reading_recomputes_and_rejects_bad_batches_atomically(store, settings):
    _, first = persist(store, settings)
    did = first.document_id
    bad = correction(did, 1).model_copy(update={"changes": [
        *correction(did, 1).changes, correction(did, 1, "o-nope", "1.00").changes[0]]})
    before = counts(store)
    with raises("invalid_correction"):
        store.apply_corrections(did, bad, fake_apply)
    with raises("invalid_correction"):  # derived values cannot be edited directly
        store.apply_corrections(did, correction(did, 1, "o-calc", "99.00"), fake_apply)
    assert counts(store) == before
    result = store.apply_corrections(did, correction(did, 1, "o-p0", "3.00"), fake_apply)
    child = result.document
    assert {o.id: o.value for o in child.observations}["o-calc"] == "5.00"
    assert any(i.code == "total_mismatch" for i in child.issues)
    old = store.get_document(did, 1)  # the immutable baseline still shows the original reading
    assert {o.id: o.value for o in old.observations}["o-p0"] == "2.00" and old.approval == "needs_review"
    assert Decimal({o.id: o.value for o in old.observations}["o-calc"]) == Decimal("4.00")


def test_approval_without_edit_requires_an_explicit_decision(store, settings):
    _, first = persist(store, settings)
    did = first.document_id
    result = store.apply_corrections(did, correction(did, 1, None, approve=True), fake_apply)
    assert result.document.approval == "human_approved" and result.document.parent_version == 1
    _, again = persist(store, settings)
    assert again.document.approval == "human_approved"  # reimport does not discard the approval


def test_unknown_base_and_foreign_document_are_rejected(store, settings):
    _, first = persist(store, settings)
    with raises("unknown_version"):
        store.apply_corrections(first.document_id, correction(first.document_id, 9), fake_apply)
    with raises("document_mismatch"):
        store.apply_corrections("doc-other", correction(first.document_id, 1), fake_apply)


def test_concurrent_different_edits_on_one_base_only_one_wins(store, settings):
    _, first = persist(store, settings)
    did = first.document_id
    barrier, outcomes = threading.Barrier(2), []

    def edit(obs, value):
        barrier.wait()
        try:
            outcomes.append(store.apply_corrections(did, correction(did, 1, obs, value), fake_apply).version)
        except Exception as exc:
            outcomes.append(getattr(exc, "code", repr(exc)))
    threads = [threading.Thread(target=edit, args=a) for a in (("o-p0", "3.00"), ("o-p1", "3.50"))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(map(str, outcomes)) == ["2", "stale_base"]
    assert [v["version"] for v in store.versions(did)] == [1, 2]


def test_domain_result_must_record_the_decision(store, settings):
    _, first = persist(store, settings)

    def forgetful(document, changes):
        data = document.model_dump(mode="json")
        data.update(version=2, parent_version=1)
        from vision_tables.contracts import Document
        return Document.model_validate(data)
    with raises("invalid_correction"):
        store.apply_corrections(first.document_id, correction(first.document_id, 1), forgetful)


# -- interruption ------------------------------------------------------------------------------------------
def test_interrupted_commit_leaves_no_partial_version(store, settings):
    job = store.create_job(kind="extract", input_path="x", output_dir="o", settings_json="{}")
    doc = build_document(job.job_id, settings)

    def boom(name):
        if name == "persist:before_commit":
            raise KeyboardInterrupt
    Store.fault_hook = staticmethod(boom)
    try:
        with pytest.raises(KeyboardInterrupt):
            store.persist_candidates(job.job_id, job.token, [doc])
    finally:
        Store.fault_hook = None
    assert all(v == 0 for v in counts(store).values())
    assert store.get_job(job.job_id)["state"] == "running"
    assert store.persist_candidates(job.job_id, job.token, [doc])[0].version == 1  # same job can simply retry


def test_interrupted_correction_commit_changes_nothing(store, settings):
    _, first = persist(store, settings)
    before = counts(store)
    Store.fault_hook = staticmethod(lambda name: (_ for _ in ()).throw(RuntimeError("disk")) if name ==
                                    "correct:before_commit" else None)
    try:
        with pytest.raises(RuntimeError):
            store.apply_corrections(first.document_id, correction(first.document_id, 1), fake_apply)
    finally:
        Store.fault_hook = None
    assert counts(store) == before
    assert store.apply_corrections(first.document_id, correction(first.document_id, 1), fake_apply).version == 2


def test_lost_lease_refuses_to_persist(store, settings):
    job = store.create_job(kind="extract", input_path="x", output_dir="o", settings_json="{}")
    with store.tx() as conn:
        conn.execute("UPDATE jobs SET lease_expires_at=0")
    [(row, token)] = store.claim_expired_jobs()
    with raises("lease_lost"):
        store.persist_candidates(job.job_id, job.token, [build_document(job.job_id, settings)])
    assert row["job_id"] == job.job_id and token != job.token
    assert counts(store)["documents"] == 0


# -- duplicates ---------------------------------------------------------------------------------------------
def test_possible_duplicate_across_photos_is_flagged_not_deleted(store, settings):
    _, a = persist(store, settings)
    _, b = persist(store, settings, source_bytes=PNG + b"second photo")
    assert a.document_id != b.document_id and b.possible_duplicates[0]["document_id"] == a.document_id
    assert counts(store)["documents"] == 2
    assert store.duplicates(a.document_id)[0]["status"] == "review"
    _, c = persist(store, settings, source_bytes=PNG + b"third", supplier="Autre", number="Z-9",
                   rows=(("Sel", "1.00"), ("Eau", "1.00")))
    assert c.possible_duplicates == []


def test_signature_ignores_unreadable_values(store, settings):
    _, got = persist(store, settings)
    assert signature(got.document)["identity"] == {"document_number": "f 001", "supplier_name": "moulin rouge"}


def test_deterministic_ids_for_source_and_scope(settings):
    one = build_document("j1", settings)
    two = build_document("j2", settings)
    assert one.document_id == two.document_id and one.run.config_hash == two.run.config_hash
    assert hashlib.sha256(PNG).hexdigest() == one.source.sha256
    assert exports  # module imports cleanly


def test_database_at_migration_001_upgrades_keeping_export_state(tmp_path):
    state = tmp_path / "old"
    state.mkdir()
    conn = sqlite3.connect(state / "vision-tables.sqlite3", isolation_level=None)
    apply_migrations(conn, "then", MIGRATIONS[:1])
    for version, state_name in ((1, "published"), (2, "pending")):  # rows written by the 001 program
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("INSERT INTO exports VALUES ('d', ?, '/o', 'csv,json,review', ?, 'sha', 'then')",
                     (version, state_name))
    conn.close()
    upgraded = Store(state)
    with upgraded.read() as c:
        rows = {r[0]: (r[1], r[2]) for r in c.execute("SELECT version, state, published_formats FROM exports")}
    assert rows == {1: ("published", "csv,json,review"), 2: ("pending", "")}


# -- review findings M1 / M2 ------------------------------------------------------------------------------
def retry_candidate(store, settings, job):
    data = build_document(job.job_id, settings, incomplete=True).model_dump(mode="json")
    data.update(validation="issues", issues=[], coverage={"processed_pages": [1, 2], "skipped_pages": [],
                                                           "source_skipped_pages": [], "incomplete_pages": []})
    data["pages"].append({**data["pages"][0], "number": 2, "image_path": "pages/0002.png"})
    from vision_tables.contracts import Document
    return Document.model_validate(data)


def test_correction_on_superseded_provisional_base_is_stale_and_edits_cannot_be_orphaned(store, settings):
    _, first = persist(store, settings, incomplete=True)
    did = first.document_id
    job = store.create_job(kind="extract", input_path="x", output_dir="o", settings_json="{}")
    got = store.persist_candidates(job.job_id, job.token, [retry_candidate(store, settings, job)])[0]
    assert (got.action, got.version) == ("superseded", 2)
    with raises("stale_base") as e:
        store.apply_corrections(did, correction(did, 1), fake_apply)
    assert counts(store)["document_versions"] == 2
    ok = store.apply_corrections(did, correction(did, 2), fake_apply)  # the live lineage is reviewable
    assert ok.version == 3
    _, again = persist(store, settings, incomplete=True)  # reimport serves the edited descendant, not v1
    assert again.version == 3 and again.action == "reused"


def test_supersession_and_a_racing_correction_have_one_winner(store, settings):
    _, first = persist(store, settings, incomplete=True)
    did = first.document_id
    job = store.create_job(kind="extract", input_path="x", output_dir="o", settings_json="{}")
    candidate = retry_candidate(store, settings, job)
    barrier, outcome = threading.Barrier(2), {}

    def retry():
        barrier.wait()
        outcome["retry"] = store.persist_candidates(job.job_id, job.token, [candidate])[0].action

    def edit():
        barrier.wait()
        try:
            outcome["edit"] = store.apply_corrections(did, correction(did, 1), fake_apply).version
        except Exception as exc:
            outcome["edit"] = exc.code
    threads = [threading.Thread(target=retry), threading.Thread(target=edit)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    served = store.get_document(did)
    if outcome["retry"] == "superseded":  # retry won: the edit was rejected, nothing orphaned
        assert outcome["edit"] == "stale_base" and served.version == 2 and not served.corrections
    else:  # edit won: reviewer work is kept and the retry is discarded
        assert outcome["retry"] == "reused" and outcome["edit"] == 2 and served.corrections
    _, reimport = persist(store, settings, incomplete=True)
    assert reimport.version == served.version


def test_earlier_accepted_extraction_stays_reviewable_after_a_changed_config_extraction(store, settings):
    _, first = persist(store, settings)
    did = first.document_id
    approved = store.apply_corrections(did, correction(did, 1, None, approve=True), fake_apply)
    assert approved.document.approval == "human_approved"
    _, newer = persist(store, settings.model_copy(update={"max_tokens": 4096}))
    assert newer.action == "new_extraction" and newer.version == 3
    # the accepted lineage is not superseded by a different configuration; further review still applies to it
    follow = store.apply_corrections(did, correction(did, 2, "o-p1", "2.25"), fake_apply)
    assert follow.base_version == 2 and follow.version == 4
    assert store.get_document(did, 2).approval == "human_approved"  # historical snapshot untouched
    with raises("stale_base"):
        store.apply_corrections(did, correction(did, 2, "o-p0", "1.00"), fake_apply)


def _export_row(store, did, version, out):
    with store.read() as conn:
        row = conn.execute("SELECT formats, published_formats, state FROM exports WHERE document_id=? AND version=?"
                           " AND output_dir=?", (did, version, str(out))).fetchone()
    return tuple(row)


def test_narrow_export_never_discharges_a_pending_obligation(store, settings, tmp_path):
    job, first = persist(store, settings)
    out = (tmp_path / "o").resolve()
    did = first.document_id
    store.register_export(did, 1, str(out), ("json", "csv", "review"))
    assert _export_row(store, did, 1, out) == ("json,csv,review", "", "pending")
    exports.publish(store, did, 1, out, ("sqlite",))  # a later single-format export to the same place
    assert _export_row(store, did, 1, out) == ("json,csv,review,sqlite", "sqlite", "pending")
    assert [r["state"] for r in store.pending_exports() if r["output_dir"] == str(out)] == ["pending"]
    done = exports.publish_pending(store)
    target = out / did / "v0001"
    assert [d["state"] for d in done if d["output_dir"] == str(out)] == ["published"]
    assert {"document.json", "document.sqlite3", "manifest.json"} <= {p.name for p in target.iterdir()}
    assert _export_row(store, did, 1, out) == ("json,csv,review,sqlite", "json,csv,review,sqlite", "published")
    store.register_export(did, 1, str(out), ("json",))  # re-registering a done format is not a new obligation
    assert _export_row(store, did, 1, out)[2] == "published"


def test_registration_racing_publication_keeps_both_the_obligation_and_the_completed_formats(store, settings, tmp_path):
    _, first = persist(store, settings)
    did, out = first.document_id, (tmp_path / "o").resolve()
    for _ in range(20):
        store.register_export(did, 1, str(out), ("json",))
        barrier = threading.Barrier(2)

        def publish():
            barrier.wait()
            exports.publish(store, did, 1, out, ("json",))

        def register():
            barrier.wait()
            store.register_export(did, 1, str(out), ("sqlite",))
        threads = [threading.Thread(target=publish), threading.Thread(target=register)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        formats, published, state = _export_row(store, did, 1, out)
        assert "sqlite" in formats and "json" in published and state == "pending"  # sqlite not yet written
        exports.publish(store, did, 1, out, ("sqlite",))
        assert _export_row(store, did, 1, out)[2] == "published"
        with store.tx() as conn:  # reset for the next round
            conn.execute("DELETE FROM exports")


def test_correction_pending_export_survives_narrow_export_then_recovers(store, settings, tmp_path):
    _, first = persist(store, settings)
    did, out = first.document_id, (tmp_path / "o").resolve()
    exports.publish(store, did, 1, out)
    result = store.apply_corrections(did, correction(did, 1), fake_apply, extra_outputs=(str(out),))
    assert result.output_dirs == [str(out)]
    exports.publish(store, did, 2, out, ("sqlite",))
    assert _export_row(store, did, 2, out)[2] == "pending"
    exports.publish_pending(store)
    assert (out / did / "v0002" / "document.json").is_file() and (out / did / "v0002" / "tables").is_dir()
