from datetime import UTC, datetime
from pathlib import Path

from trax_io_feature_store import TenantContext
from trax_io_reco.data.extract_loader import build_stores_from_extract

from trax_io_spine.supervisor import Supervisor
from trax_io_spine.writeback.target import InMemoryWritebackTarget

_SAMPLE = (
    Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"
)


def test_shadow_run_logs_but_applies_nothing():
    fs, inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    wb = InMemoryWritebackTarget()
    sup = Supervisor(feature_store=fs, inventory_state=inv, writeback=wb, shadow=True)
    res = sup.run(
        tenant=TenantContext(tenant_id=tid), keys=keys, now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    assert res.summary["written"] == 0
    assert res.summary["shadowed"] >= 1
    assert len(res.shadowed) == res.summary["shadowed"]
    assert wb.history == []  # nothing applied


def test_default_run_is_unchanged():
    fs, inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id="acme")
    sup = Supervisor(feature_store=fs, inventory_state=inv)
    res = sup.run(
        tenant=TenantContext(tenant_id=tid), keys=keys, now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    assert res.summary["shadowed"] == 0
