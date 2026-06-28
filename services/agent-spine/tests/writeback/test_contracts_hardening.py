from datetime import UTC, datetime

from trax_io_reco.contracts.enums import AutonomyTier

from trax_io_spine.contracts import (
    HistoryEntry,
    RollbackRequest,
    RollbackResult,
    RollbackStatus,
    WritebackRequest,
    WritebackStatus,
)


def test_writeback_request_backward_compatible_defaults():
    req = WritebackRequest(
        tenant_id="acme", pn="P1", location="YYZ", rop=5, eoq=10, safety_stock=2,
        max_stock=20, provenance_id="prov-1", idempotency_key="k1",
    )
    assert req.tier is None and req.shadow is False


def test_writeback_request_carries_tier_and_shadow():
    req = WritebackRequest(
        tenant_id="acme", pn="P1", location="YYZ", rop=5, eoq=10, safety_stock=2,
        max_stock=20, provenance_id="prov-1", idempotency_key="k1",
        tier=AutonomyTier.BOUNDED, shadow=True,
    )
    assert req.tier is AutonomyTier.BOUNDED and req.shadow is True


def test_shadowed_status_exists():
    assert WritebackStatus.SHADOWED.value == "shadowed"


def test_history_entry_round_trips():
    e = HistoryEntry(
        tenant_id="acme", pn="P1", location="YYZ", version=1, status=WritebackStatus.WRITTEN,
        old_values=None, new_values={"rop": 5, "eoq": 10, "safety_stock": 2, "max_stock": 20},
        provenance_id="prov-1", tier=AutonomyTier.BOUNDED, agent_version="agent-spine-v1",
        changed_by_principal="agent-spine", idempotency_key="k1", parent_version=None,
        changed_at=datetime(2026, 4, 1, tzinfo=UTC),
    )
    assert HistoryEntry.model_validate_json(e.model_dump_json()) == e


def test_rollback_request_and_result():
    req = RollbackRequest(
        tenant_id="acme", pn="P1", location="YYZ", reason="bad rec",
        requested_at=datetime(2026, 4, 2, tzinfo=UTC),
    )
    assert req.principal == "planner"
    res = RollbackResult(
        tenant_id="acme", pn="P1", location="YYZ", status=RollbackStatus.ROLLED_BACK,
        from_values={"rop": 7}, to_values={"rop": 5}, reverted_from_version=2, new_version=3,
        rolled_back_at=datetime(2026, 4, 2, tzinfo=UTC),
    )
    assert res.status is RollbackStatus.ROLLED_BACK
