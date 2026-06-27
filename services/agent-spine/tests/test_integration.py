from datetime import UTC, datetime
from pathlib import Path

from trax_io_feature_store import TenantContext
from trax_io_reco.data.extract_loader import build_stores_from_extract

from trax_io_spine.supervisor import Supervisor
from trax_io_spine.writeback.target import InMemoryWritebackTarget

_SAMPLE = (
    Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"
)


def test_end_to_end_orchestration_routes_recommendations() -> None:
    fs, inv, tenant_id, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    writeback = InMemoryWritebackTarget()
    sup = Supervisor(feature_store=fs, inventory_state=inv, writeback=writeback)
    res = sup.run(
        tenant=TenantContext(tenant_id=tenant_id), keys=keys,
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    total = res.summary["recommendations"]
    routed = (
        res.summary["written"] + res.summary["deferred"] + res.summary["failed"]
        + res.summary["queued"] + res.summary["rejected"]
    )
    assert total >= 1
    assert routed == total  # every recommendation lands in exactly one bucket
    # writes that happened are recorded in the target's history
    assert len(writeback.history) == res.summary["written"]


def test_cross_tenant_run_writes_nothing() -> None:
    fs, inv, tenant_id, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    writeback = InMemoryWritebackTarget()
    sup = Supervisor(feature_store=fs, inventory_state=inv, writeback=writeback)
    # A different tenant has no data in `fs` -> every key skipped, nothing written.
    res = sup.run(
        tenant=TenantContext(tenant_id="other-airline"), keys=keys,
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    assert res.summary["written"] == 0
    assert writeback.history == []
    assert res.summary["skipped"] == len(keys)
