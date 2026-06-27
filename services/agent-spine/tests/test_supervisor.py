from datetime import UTC, datetime

from trax_io_feature_store import InMemoryFeatureStore, TenantContext
from trax_io_reco.contracts.recommendation import Recommendation, RecommendationBatch

from tests.conftest import make_current, make_policy
from trax_io_spine.contracts import WritebackStatus
from trax_io_spine.supervisor import Supervisor, to_writeback_request
from trax_io_spine.writeback.target import InMemoryWritebackTarget


class _FakeService:
    """Stands in for #11's RecommendationService.run, returning a fixed batch."""

    def __init__(self, recs: tuple[Recommendation, ...]) -> None:
        self._recs = recs

    def run(self, *, tenant, keys, now, reporting_horizon_days=30) -> RecommendationBatch:  # noqa: ANN001
        from trax_io_reco.contracts.recommendation import BatchSummary

        return RecommendationBatch(
            tenant_id=tenant.tenant_id, generated_at=now, recommendations=self._recs,
            skipped=(), summary=BatchSummary(total=len(self._recs)),
        )


def test_to_writeback_request_maps_policy(make_rec) -> None:
    rec = make_rec(policy=make_policy(rop=7, eoq=3, safety_stock=2, max_stock=15))
    req = to_writeback_request(rec, idempotency_key="k1")
    assert (req.rop, req.eoq, req.safety_stock, req.max_stock) == (7, 3, 2, 15)
    assert req.idempotency_key == "k1"


def test_supervisor_routes_and_writes(make_rec) -> None:
    approved = make_rec(
        recommendation_id="r-approve",
        policy=make_policy(max_stock=23), current_policy=make_current(max_stock=20),  # +15%
    )
    writeback = InMemoryWritebackTarget()
    sup = Supervisor(
        feature_store=InMemoryFeatureStore(), inventory_state=None,
        writeback=writeback, service=_FakeService((approved,)),
    )
    res = sup.run(
        tenant=TenantContext(tenant_id="acme"), keys=[("PN-A", "LOC-1")],
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    assert len(res.written) == 1
    assert res.written[0].status is WritebackStatus.WRITTEN
    assert res.summary["written"] == 1
