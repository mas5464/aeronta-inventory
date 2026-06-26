from __future__ import annotations

from datetime import datetime

import pytest
from trax_io_feature_store import TenantContext

from tests.fixtures import scenarios
from trax_io_reco.service import RecommendationService

NOW = datetime(2026, 4, 17, 9, 0, 0)


def _batch_provider():
    fs, inv, tenant_id, keys = scenarios.scenario_1_demand_exceeds_stock()
    svc = RecommendationService(feature_store=fs, inventory_state=inv)

    def run_batch(tenant: str, reporting_horizon: int):
        return svc.run(tenant=TenantContext(tenant_id=tenant), keys=keys, now=NOW,
                       reporting_horizon_days=reporting_horizon)

    return run_batch


def test_api_lists_recommendations() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from trax_io_reco.api.app import create_app

    app = create_app(_batch_provider())
    client = TestClient(app)
    resp = client.get("/v1/recommendations", params={"tenant": "acme"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == "acme"
    assert len(body["recommendations"]) >= 1


def test_api_min_confidence_filter() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from trax_io_reco.api.app import create_app

    client = TestClient(create_app(_batch_provider()))
    resp = client.get("/v1/recommendations", params={"tenant": "acme", "min_confidence": 1.01})
    assert resp.status_code == 200
    assert resp.json()["recommendations"] == []  # nothing scores above 1.0
