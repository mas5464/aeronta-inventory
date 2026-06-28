from datetime import UTC, datetime
from pathlib import Path

import pytest

from trax_io_spine.bff.models import RejectReason, TaskStatus
from trax_io_spine.bff.store import KillSwitchEngaged, PlannerStore
from trax_io_spine.contracts import WritebackStatus

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


def test_approve_writes_and_flips_status():
    store = _store()
    with_p, _ = _ids_by_policy(store)
    res = store.approve(with_p[0])
    assert res.status is TaskStatus.APPROVED
    assert res.writeback is not None and res.writeback.status is WritebackStatus.WRITTEN
    assert store.detail(with_p[0]).status is TaskStatus.APPROVED
    assert len(store.writeback.get_history(
        tenant_id="acme", pn=res.writeback.pn, location=res.writeback.location)) == 1


def test_approve_no_policy_rec_raises():
    store = _store()
    _, without_p = _ids_by_policy(store)
    if not without_p:
        pytest.skip("sample produced no non-policy queued recs")
    with pytest.raises(ValueError):
        store.approve(without_p[0])


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


def test_approve_while_killswitch_engaged_raises():
    store = _store()
    with_p, _ = _ids_by_policy(store)
    store.set_kill_switch(True)
    with pytest.raises(KillSwitchEngaged):
        store.approve(with_p[0])
