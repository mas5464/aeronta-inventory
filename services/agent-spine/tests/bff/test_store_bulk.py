from datetime import UTC, datetime
from pathlib import Path

import pytest

from trax_io_spine.bff.models import BulkApproveFilter, TaskStatus
from trax_io_spine.bff.store import KillSwitchEngaged, PlannerStore
from trax_io_spine.contracts import RollbackRequest, RollbackStatus

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def _store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )


def test_bulk_approve_approves_policy_bearing_pending(seed_pending_recommendations):
    store = _store()
    seed_pending_recommendations(store)
    pending_before = len(store.queue())
    count, results = store.bulk_approve(BulkApproveFilter())
    assert count == len(results) >= 1
    assert all(r.status is TaskStatus.APPROVED for r in results)
    assert len(store.queue()) < pending_before  # approved ones left the pending queue


def test_bulk_approve_blocked_by_killswitch():
    store = _store()
    store.set_kill_switch(True)
    with pytest.raises(KillSwitchEngaged):
        store.bulk_approve(BulkApproveFilter())


def test_history_and_rollback_round_trip(seed_pending_recommendations):
    store = _store()
    seed_pending_recommendations(store, count=1)
    count, results = store.bulk_approve(BulkApproveFilter())
    wb = next(r.writeback for r in results if r.writeback is not None)
    hist = store.history(pn=wb.pn, location=wb.location)
    assert len(hist) >= 1
    res = store.rollback(RollbackRequest(
        tenant_id="acme", pn=wb.pn, location=wb.location, reason="planner undo",
        requested_at=datetime.now(UTC),
    ))
    assert res.status in (RollbackStatus.ROLLED_BACK, RollbackStatus.NOTHING_TO_REVERT)
