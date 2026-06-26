from __future__ import annotations

from datetime import datetime

from trax_io_feature_store import InMemoryFeatureStore, TenantContext

from tests.fixtures.builders import seed_part
from trax_io_reco.contracts.enums import RecommendationType
from trax_io_reco.data.inventory_state import InMemoryInventoryState
from trax_io_reco.service import RecommendationService

TENANT = TenantContext(tenant_id="acme")
NOW = datetime(2026, 4, 17, 9, 0, 0)


def _service_with_shortage() -> RecommendationService:
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    seed_part(fs, inv, tenant_id="acme", pn="P-100", location="YYZ", monthly_units=[20] * 12,
              serviceable=2, lead_mean_days=60.0, current_policy=(5, 5, 2, 40))
    return RecommendationService(feature_store=fs, inventory_state=inv)


def test_service_emits_purchase_for_shortage() -> None:
    svc = _service_with_shortage()
    batch = svc.run(tenant=TENANT, keys=[("P-100", "YYZ")], now=NOW)
    assert batch.summary.total >= 1
    types = {r.type for r in batch.recommendations}
    assert RecommendationType.PURCHASE in types
    for r in batch.recommendations:
        assert r.description and r.reason and r.supporting_evidence
        assert 0.0 <= r.confidence_score <= 1.0


def test_service_is_deterministic() -> None:
    svc = _service_with_shortage()
    b1 = svc.run(tenant=TENANT, keys=[("P-100", "YYZ")], now=NOW)
    b2 = svc.run(tenant=TENANT, keys=[("P-100", "YYZ")], now=NOW)
    # input_snapshot_hash and ordering/fields identical modulo recommendation_id.
    h1 = [r.input_snapshot_hash for r in b1.recommendations]
    h2 = [r.input_snapshot_hash for r in b2.recommendations]
    assert h1 == h2 and len(h1) >= 1
    assert [r.type for r in b1.recommendations] == [r.type for r in b2.recommendations]


def test_service_skips_missing_key() -> None:
    svc = _service_with_shortage()
    batch = svc.run(tenant=TENANT, keys=[("P-100", "YYZ"), ("MISSING", "ZZZ")], now=NOW)
    assert any(s.pn == "MISSING" for s in batch.skipped)
