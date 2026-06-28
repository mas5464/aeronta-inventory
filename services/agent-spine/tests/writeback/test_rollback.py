from datetime import UTC, datetime, timedelta

import pytest

from trax_io_spine.contracts import RollbackRequest, RollbackStatus, WritebackRequest
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def _req(key, *, rop):
    return WritebackRequest(
        tenant_id="acme", pn="P1", location="YYZ", rop=rop, eoq=10, safety_stock=2,
        max_stock=20, provenance_id="prov-1", idempotency_key=key,
    )


def _rollback(*, at=None):
    return RollbackRequest(
        tenant_id="acme", pn="P1", location="YYZ", reason="bad rec",
        requested_at=at or datetime.now(UTC),
    )


def test_zero_window_is_rejected():
    with pytest.raises(ValueError):
        InMemoryWritebackTarget(rollback_window_days=0)


def test_rollback_reverts_latest_write_to_prior_values():
    t = InMemoryWritebackTarget()
    t.write(_req("k1", rop=5))
    t.write(_req("k2", rop=7))
    res = t.rollback(_rollback())
    assert res.status is RollbackStatus.ROLLED_BACK
    assert res.from_values["rop"] == 7
    assert res.to_values["rop"] == 5
    assert res.reverted_from_version == 2 and res.new_version == 3
    # the level is back to 5, and a new chained entry was logged
    after = t.write(_req("k3", rop=9))
    assert after.old_values["rop"] == 5
    hist = t.get_history(tenant_id="acme", pn="P1", location="YYZ")
    assert hist[2].parent_version == 2  # the rollback entry links to v2


def test_rollback_with_no_prior_write_is_nothing_to_revert():
    t = InMemoryWritebackTarget()
    assert t.rollback(_rollback()).status is RollbackStatus.NOTHING_TO_REVERT


def test_rollback_of_only_first_write_is_nothing_to_revert():
    t = InMemoryWritebackTarget()
    t.write(_req("k1", rop=5))  # old_values is None -> nothing to revert to
    assert t.rollback(_rollback()).status is RollbackStatus.NOTHING_TO_REVERT


def test_rollback_outside_window():
    t = InMemoryWritebackTarget(rollback_window_days=30)
    t.write(_req("k1", rop=5))
    t.write(_req("k2", rop=7))
    far_future = datetime.now(UTC) + timedelta(days=31)
    res = t.rollback(_rollback(at=far_future))
    assert res.status is RollbackStatus.OUTSIDE_WINDOW
    # nothing mutated
    after = t.write(_req("k3", rop=9))
    assert after.old_values["rop"] == 7
