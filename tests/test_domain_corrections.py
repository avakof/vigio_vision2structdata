import pytest

from vision_tables.config import ProcessingError
from vision_tables.contracts import Correction, CorrectionSet, Document, FieldRead, Issue, PageReading
from vision_tables.domain import apply_corrections, assemble, review_gate, revalidate
from vision_tables.domain.examples import EXAMPLES, example_context, f, rec


def make(reading=None, kind="invoice"):
    ingested, run = example_context()
    return assemble(ingested, [reading or EXAMPLES[kind]()], [1], run, "doc-test")


def edit(document, *changes, approve=False, reviewer="Reviewer One"):
    return CorrectionSet(document_id=document.document_id, base_version=document.version, reviewer=reviewer,
                         changes=list(changes), approve=approve)


def fix(observation_id, value, raw=None, state="readable", reason="checked against the page"):
    return Correction(observation_id=observation_id, value=value, raw=raw, state=state, reason=reason)


def obs(document, oid):
    return next(o for o in document.observations if o.id == oid)


def issue_codes(document, severity=None):
    return [i.code for i in document.issues if severity in (None, i.severity)]


def test_pack_correction_rederives_and_keeps_every_original_inspectable():
    v1 = make()
    snapshot = v1.model_dump_json()
    v2 = apply_corrections(v1, edit(v1, fix("p1.l1.pack_count", "5", raw="5")))
    assert v1.model_dump_json() == snapshot                       # the input version is untouched
    assert (v2.version, v2.parent_version, v2.approval) == (2, 1, "needs_review")
    assert obs(v1, "p1.l1.pack_total_quantity").value == "12" and obs(v2, "p1.l1.pack_total_quantity").value == "10"
    assert obs(v2, "p1.l1.pack_total_quantity").derivation.inputs == obs(v1, "p1.l1.pack_total_quantity").derivation.inputs
    corrected = obs(v2, "p1.l1.pack_count")
    assert corrected.origin == "human_supplied" and corrected.value == "5"
    assert "model read '6'" in corrected.reason and "Reviewer One" in corrected.reason
    assert [e.channel for e in corrected.evidence] == ["direct", "human"] and corrected.evidence[0].quote == "carton de 6 bouteilles"
    assert obs(v1, "p1.l1.pack_count").origin == "observed"       # the original model observation lives in v1
    # the discrepancy introduced by the edit is visible: 10 L x 0,60 = 6,00 but the printed line total is 7,20
    assert obs(v2, "p1.l1.calculated_line_total").value == "6.00"
    assert "line_total_mismatch" in issue_codes(v2, "review") and "line_total_mismatch" not in issue_codes(v1)
    assert v2.corrections == [edit(v1, fix("p1.l1.pack_count", "5", raw="5"))]
    assert Document.model_validate(v2.model_dump(mode="json")) == v2


def test_replay_is_deterministic_and_history_is_append_only():
    v1 = make()
    change = edit(v1, fix("p1.l1.pack_count", "5"))
    assert apply_corrections(v1, change) == apply_corrections(v1, change)
    v2 = apply_corrections(v1, change)
    v3 = apply_corrections(v2, edit(v2, fix("p1.l1.line_total", "6.00", raw="6,00")))
    assert (v3.version, v3.parent_version, len(v3.corrections)) == (3, 2, 2)
    assert "line_total_mismatch" not in issue_codes(v3, "review")
    assert obs(v3, "p1.l1.pack_count").origin == "human_supplied"
    with pytest.raises(ProcessingError) as error:
        apply_corrections(v2, change)                             # the stale base is rejected
    assert error.value.code == "stale_base"


def test_invalid_batches_apply_nothing():
    v1 = make()
    snapshot = v1.model_dump_json()
    for bad, code in [(fix("p1.l1.pack_total_quantity", "99"), "invalid_correction"),
                      (fix("p1.l1.nope", "1"), "invalid_correction"),
                      (fix("p1.l1.quantity", "1,5"), "invalid_correction"),
                      (fix("p1.inv.issue_date", "03/04/2026"), "invalid_correction"),
                      (fix("p1.inv.currency", "$"), "invalid_correction"),
                      (fix("p1.l1.unit_price", "1e3"), "invalid_correction")]:
        with pytest.raises(ProcessingError) as error:
            apply_corrections(v1, edit(v1, fix("p1.l1.pack_count", "5"), bad))
        assert error.value.code == code
    assert v1.model_dump_json() == snapshot
    with pytest.raises(ProcessingError) as error:
        apply_corrections(v1, CorrectionSet(document_id="other", base_version=1, reviewer="r", approve=True))
    assert error.value.code == "document_mismatch"


def test_typed_values_by_lexical_type():
    v1 = make()
    ok = apply_corrections(v1, edit(v1, fix("p1.inv.issue_date", "2026-04-04"), fix("p1.inv.currency", "EUR"),
                                    fix("p1.l1.quantity", "3"), fix("p1.l1.unit_price", "0.65"), fix("p1.t1.rate", "5.5"),
                                    fix("p1.l1.purchase_unit", "carton"), fix("p1.inv.supplier_name", "Maison Dupont")))
    assert obs(ok, "p1.inv.issue_date").value == "2026-04-04"
    assert obs(ok, "p1.l1.pack_total_quantity").value == "18"
    for bad in (fix("p1.inv.issue_date", "2026-02-30"), fix("p1.l1.pack_count", "0"), fix("p1.t1.rate", "120"),
                fix("p1.inv.document_kind", "bill"), fix("p1.l1.purchase_unit", "furlong"), fix("p1.inv.supplier_name", "  x ")):
        with pytest.raises(ProcessingError):
            apply_corrections(v1, edit(v1, bad))
    menu = make(kind="menu")
    flag = apply_corrections(menu, edit(menu, fix("p1.i1.name", "Soupe", raw="Soupe")))
    assert obs(flag, "p1.i1.name").value == "Soupe"
    with pytest.raises(ProcessingError):
        apply_corrections(menu, edit(menu, fix("p1.c1.item_ref", "p1.s1")))
    assert obs(apply_corrections(menu, edit(menu, fix("p1.c1.item_ref", "p1.i1"))), "p1.c1.item_ref").value == "p1.i1"


def test_a_reviewer_can_resolve_an_ambiguous_reading_or_state_it_unresolved():
    reading = PageReading(page=1, doc_type="invoice", records=[
        rec("invoice", "i", issue_date=f("03/04/2026"), total=f("5,00"), currency=f("EUR"), document_number=f("1"), supplier_name=f("S")),
        rec("line_item", "a", "i", description=f("x"), quantity=f("1"), unit_price=f("5,00"), line_total=f("5,00"))])
    v1 = make(reading)
    assert obs(v1, "p1.i.issue_date").state == "ambiguous" and "ambiguous_reading" in issue_codes(v1, "review")
    resolved = apply_corrections(v1, edit(v1, fix("p1.i.issue_date", "2026-04-03", raw="03/04/2026")))
    assert "ambiguous_reading" not in issue_codes(resolved) and resolved.validation == "valid"
    still = apply_corrections(v1, edit(v1, fix("p1.i.issue_date", None, state="unreadable", reason="illegible stamp")))
    assert obs(still, "p1.i.issue_date").state == "unreadable"
    assert "unreadable_field" in issue_codes(still, "review")        # a critical field still blocks approval
    with pytest.raises(ProcessingError) as error:
        apply_corrections(still, edit(still, approve=True))
    assert error.value.code == "approval_blocked" and "unreadable_field" in str(error.value)


def test_approval_needs_a_human_decision_on_a_clear_exact_version():
    v1 = make()
    assert review_gate(v1)["approvable"] and v1.approval == "needs_review"
    v2 = apply_corrections(v1, edit(v1, approve=True))              # approve unchanged: no fabricated edit
    assert v2.approval == "human_approved" and v2.version == 2 and v2.corrections[-1].approve and v2.validation == "valid"
    assert v2.observations == v1.observations
    assert [i for i in v2.issues] == [i for i in v1.issues]         # informational notes are retained, not cleared
    assert revalidate(v2) == v2
    again = apply_corrections(v2, edit(v2, fix("p1.l2.line_total", "18.60")))
    assert again.approval == "needs_review"                         # a later edit resets approval
    blocked = make(EXAMPLES["invoice"]().model_copy(update={"suspected_cutoff": True}))
    with pytest.raises(ProcessingError) as error:
        apply_corrections(blocked, edit(blocked, approve=True))
    assert error.value.code == "approval_blocked"
    incomplete = make(EXAMPLES["invoice"]().model_copy(update={"incomplete": True}))
    with pytest.raises(ProcessingError):
        apply_corrections(incomplete, edit(incomplete, approve=True))


def test_edit_and_approve_in_one_decision_requires_the_edited_result_to_be_clear():
    v1 = make()
    broke = edit(v1, fix("p1.l2.line_total", "99.00"), approve=True)
    with pytest.raises(ProcessingError) as error:
        apply_corrections(v1, broke)
    assert error.value.code == "approval_blocked" and "line_total_mismatch" in str(error.value)
    cleaner = apply_corrections(v1, edit(v1, fix("p1.l2.description", "Farine T55 (25 kg)"), approve=True))
    assert cleaner.approval == "human_approved"


def test_revalidating_a_tampered_approved_version_drops_the_approval():
    v1 = make()
    approved = apply_corrections(v1, edit(v1, approve=True))
    tampered = approved.model_copy(update={"observations": [
        o.model_copy(update={"value": "99.00"}) if o.id == "p1.l2.line_total" else o for o in approved.observations]})
    result = revalidate(tampered)
    assert result.approval == "needs_review" and "line_total_mismatch" in issue_codes(result, "review")


def test_revalidate_recomputes_after_direct_changes_and_keeps_carried_issues():
    v1 = make(EXAMPLES["invoice"]().model_copy(update={"issues": [Issue(code="adapter_note", message="kept", page=1)]}))
    edited = v1.model_copy(update={"observations": [o.model_copy(update={"value": "30"}) if o.id == "p1.l2.quantity" else o
                                                    for o in v1.observations]})
    fresh = revalidate(edited)
    assert obs(fresh, "p1.l2.calculated_line_total").value == "555.00" and "adapter_note" in issue_codes(fresh)
    assert "line_total_mismatch" in issue_codes(fresh, "review")
    assert revalidate(fresh) == fresh


def test_correcting_an_observation_retires_its_evidence_issue_only():
    reading = PageReading(page=1, doc_type="invoice", records=[rec("invoice", "i",
        total=FieldRead(raw="5,00", evidence="5,00", region_id="ghost"), currency=f("EUR"))])
    v1 = make(reading)
    assert "invalid_evidence" in issue_codes(v1, "review")
    v2 = apply_corrections(v1, edit(v1, fix("p1.i.total", "5.00", raw="5,00")))
    assert "invalid_evidence" not in issue_codes(v2)
