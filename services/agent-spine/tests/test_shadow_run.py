from datetime import UTC, datetime
from pathlib import Path

from trax_io_feature_store import InMemoryFeatureStore, TenantContext
from trax_io_reco.contracts.recommendation import BatchSummary, RecommendationBatch
from trax_io_reco.data.extract_loader import build_stores_from_extract

from tests.conftest import make_current, make_policy
from trax_io_spine.supervisor import Supervisor
from trax_io_spine.writeback.target import InMemoryWritebackTarget

_SAMPLE = (
    Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"
)


class _OneRecommendationService:
    def __init__(self, recommendation) -> None:
        self._recommendation = recommendation

    def run(self, *, tenant, keys, now, reporting_horizon_days=30):  # noqa: ANN001
        del keys
        return RecommendationBatch(
            tenant_id=tenant.tenant_id,
            generated_at=now,
            reporting_horizon_days=reporting_horizon_days,
            recommendations=(self._recommendation,),
            summary=BatchSummary(total=1),
        )


def test_shadow_run_logs_but_applies_nothing(make_rec):
    recommendation = make_rec(
        policy=make_policy(max_stock=21),
        current_policy=make_current(max_stock=20),
    )
    wb = InMemoryWritebackTarget()
    sup = Supervisor(
        feature_store=InMemoryFeatureStore(),
        inventory_state=None,
        writeback=wb,
        service=_OneRecommendationService(recommendation),
        shadow=True,
    )
    res = sup.run(
        tenant=TenantContext(tenant_id="acme"),
        keys=[("PN-A", "LOC-1")],
        now=datetime(2026, 4, 1, tzinfo=UTC),
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
