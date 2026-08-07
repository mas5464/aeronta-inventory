from datetime import UTC, datetime
from pathlib import Path

import pytest
from trax_io_reco.contracts.enums import AutonomyTier

from trax_io_spine.bff.models import RejectReason, TaskStatus
from trax_io_spine.bff.store import KillSwitchEngaged, PlannerStore, _Entry
from trax_io_spine.contracts import GuardrailOutcome, GuardrailStatus, WritebackStatus
from trax_io_spine.guardrail.enforce import GuardrailEnforcer

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def _store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )


def _ids_by_policy(store):
    with_p, without_p = [], []
    for row in store.queue():
        (with_p if store.detail(row.recommendation_id).proposed_policy else without_p).append(
            row.recommendation_id
        )
    return with_p, without_p


def _pending_policy_store(make_rec):
    store = PlannerStore(tenant_id="acme")
    rec = make_rec(
        recommendation_id="r-pending-policy",
        suggested_autonomy_tier=AutonomyTier.ADVISOR,
    )
    store._ingest(rec, GuardrailEnforcer().enforce(rec))
    return store, rec.recommendation_id


def test_approve_writes_and_flips_status(make_rec):
    store, rec_id = _pending_policy_store(make_rec)
    res = store.approve(rec_id)
    assert res.status is TaskStatus.APPROVED
    assert res.writeback is not None and res.writeback.status is WritebackStatus.WRITTEN
    assert store.detail(rec_id).status is TaskStatus.APPROVED
    assert len(store.writeback.get_history(
        tenant_id="acme", pn=res.writeback.pn, location=res.writeback.location)) == 1


def test_approve_no_policy_rec_raises():
    store = _store()
    _, without_p = _ids_by_policy(store)
    if not without_p:
        pytest.skip("sample produced no non-policy queued recs")
    with pytest.raises(ValueError):
        store.approve(without_p[0])


def test_binding_open_order_is_seeded_deferred_and_cannot_be_approved(make_rec):
    store = PlannerStore(tenant_id="acme")
    rec = make_rec(
        recommendation_id="r-open-order",
        guardrail_flags=("open_order_deferral",),
    )
    outcome = GuardrailEnforcer().enforce(rec)

    store._ingest(rec, outcome)

    assert store.detail(rec.recommendation_id).status is TaskStatus.DEFERRED
    assert store.writeback.history == []
    with pytest.raises(ValueError, match="not pending approval"):
        store.approve(rec.recommendation_id)
    assert store.writeback.history == []


def test_reject_records_reason():
    store = _store()
    rec_id = store.queue()[0].recommendation_id
    res = store.reject(rec_id, RejectReason.WRONG_FOR_FLEET, "not for this fleet")
    assert res.status is TaskStatus.REJECTED
    assert store.detail(rec_id).status is TaskStatus.REJECTED


def test_defer_sets_status():
    store = _store()
    rec_id = store.queue()[0].recommendation_id
    assert store.defer(rec_id).status is TaskStatus.DEFERRED
    assert store.detail(rec_id).status is TaskStatus.DEFERRED


def test_approve_while_killswitch_engaged_raises(make_rec):
    store, rec_id = _pending_policy_store(make_rec)
    store.set_kill_switch(True)
    with pytest.raises(KillSwitchEngaged):
        store.approve(rec_id)


def test_reason_is_always_the_recommender_reason(make_rec) -> None:
    store = _store()
    rec = make_rec(
        recommendation_id="r-reason-fix", reason="Recompute levels for steady demand.",
    )
    outcome = GuardrailOutcome(
        recommendation_id="r-reason-fix", status=GuardrailStatus.REJECTED_HARD_GUARDRAIL,
        tier=AutonomyTier.ADVISOR, delta_pct=1.5,
        reasons=("delta_exceeds_100pct", "delta_gt_100pct"),
    )
    store._entries["r-reason-fix"] = _Entry(rec, outcome, TaskStatus.PENDING)

    detail = store.detail("r-reason-fix")
    assert detail.reason == "Recompute levels for steady demand."

    row = store._row(store._entries["r-reason-fix"])
    assert row.reason == "Recompute levels for steady demand."


def test_guardrail_notes_are_humanized_and_deduped(make_rec) -> None:
    store = _store()
    rec = make_rec(recommendation_id="r-notes-fix", reason="test reason")
    outcome = GuardrailOutcome(
        recommendation_id="r-notes-fix", status=GuardrailStatus.REJECTED_HARD_GUARDRAIL,
        tier=AutonomyTier.ADVISOR, delta_pct=1.5,
        reasons=("delta_exceeds_100pct", "delta_gt_100pct", "active_aog"),
    )
    store._entries["r-notes-fix"] = _Entry(rec, outcome, TaskStatus.PENDING)

    detail = store.detail("r-notes-fix")
    assert detail.guardrail_notes == (
        "Exceeds the 100% single-write cap — requires manual review.",
        "An aircraft is currently AOG for this part — routed for immediate review.",
    )


def test_guardrail_notes_empty_for_non_policy_advisory(make_rec) -> None:
    store = _store()
    rec = make_rec(recommendation_id="r-advisory-fix", reason="Advisory reason.", policy=None)
    outcome = GuardrailOutcome(
        recommendation_id="r-advisory-fix", status=GuardrailStatus.QUEUED_FOR_APPROVAL,
        tier=AutonomyTier.ADVISOR, delta_pct=0.0, reasons=("non_policy_recommendation",),
    )
    store._entries["r-advisory-fix"] = _Entry(rec, outcome, TaskStatus.PENDING)

    detail = store.detail("r-advisory-fix")
    assert detail.reason == "Advisory reason."
    assert detail.guardrail_notes == ()
