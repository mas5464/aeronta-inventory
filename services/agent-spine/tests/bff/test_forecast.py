"""Slice S5 — Forecast & Service Levels: store method + BFF route."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from trax_io_reco.demand.basis import demand_basis_trace

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.store import PlannerStore

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def _store() -> PlannerStore:
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )


def _client():
    store = _store()
    return TestClient(create_planner_app({"acme": store})), store


def test_forecast_summary_service_levels_match_real_policy_and_key_counts():
    store = _store()
    fc = store.forecast_summary()

    bands = fc.service_levels.bands
    # Every configured tier (1..5) appears, in tier order, target straight from the
    # real TenantPolicyConfig default policy (spec §5.3).
    assert [b.criticality_tier for b in bands] == [1, 2, 3, 4, 5]
    expected_targets = {1: 0.995, 2: 0.98, 3: 0.95, 4: 0.92, 5: 0.90}
    for b in bands:
        assert b.target_service_level == expected_targets[b.criticality_tier]
        assert b.sku_count >= 0

    # sku_count sums to the number of keys that have a resolvable criticality tier.
    t = store.tenant

    def _crit(pn: str):
        try:
            return store.fs.get_criticality(tenant=t, pn=pn)
        except Exception:  # noqa: BLE001
            return None

    with_crit = sum(1 for pn, _loc in store.keys if _crit(pn) is not None)
    assert sum(b.sku_count for b in bands) == with_crit


def test_forecast_summary_actual_coverage_bounded_or_none():
    store = _store()
    fc = store.forecast_summary()
    for band in fc.service_levels.bands:
        if band.sku_count == 0:
            assert band.actual_coverage is None
        else:
            assert band.actual_coverage is not None
            assert 0.0 <= band.actual_coverage <= 1.0


def test_forecast_summary_method_coverage_real_regime_classification():
    store = _store()
    fc = store.forecast_summary()

    coverage = fc.method_coverage
    assert coverage.total_skus == sum(r.sku_count for r in coverage.rows)
    valid_regimes = {"ultra_rare", "intermittent", "moderate", "high_volume"}
    for row in coverage.rows:
        assert row.regime in valid_regimes
        assert row.method  # non-empty honest method label
        assert row.sku_count > 0
        assert 0.0 <= row.pct <= 1.0
    if coverage.total_skus:
        assert abs(sum(r.pct for r in coverage.rows) - 1.0) < 1e-9


def test_forecast_summary_accuracy_is_honest_proxy_not_fabricated():
    store = _store()
    fc = store.forecast_summary()

    acc = fc.accuracy
    assert acc.status == "proxy"
    assert "backtest" in acc.note.lower()
    for point in acc.points:
        assert point.actual >= 0.0
        assert point.projected >= 0.0
    # at most the two most recent distinct periods present in the real extract
    assert len(acc.points) <= 2


def test_forecast_summary_accuracy_projected_scales_with_period_length():
    """S5 review fix: `projected` must be a genuine per-period value — the
    portfolio's constant-rate (mean-per-day) projection scaled by EACH period's
    own real length in days — not one total split evenly across periods. Regression
    guard for the bug where every point showed an identical flat `projected`
    regardless of period length (verified live: two 2025-11-01/2025-12-01 points
    both showed 10.1096... before the fix)."""
    import calendar
    from datetime import date

    store = _store()
    fc = store.forecast_summary()

    points = fc.accuracy.points
    assert len(points) == 2, "sample extract must yield two distinct monthly periods"

    days = [calendar.monthrange(date.fromisoformat(p.period_start).year,
                                 date.fromisoformat(p.period_start).month)[1]
            for p in points]
    assert days[0] != days[1], "test needs two unequal-length months to be meaningful"

    # projected must be proportional to each period's real day-count, not identical.
    assert points[0].projected != points[1].projected
    rate_0 = points[0].projected / days[0]
    rate_1 = points[1].projected / days[1]
    assert abs(rate_0 - rate_1) < 1e-9  # same constant daily rate underlies both periods
    ratio = points[1].projected / points[0].projected
    assert abs(ratio - (days[1] / days[0])) < 1e-9


def test_forecast_accuracy_uses_historical_basis_not_recommendation_projection():
    import calendar
    from datetime import date

    store = _store()
    forecast = store.forecast_summary()
    keys_with_recommendations = {
        (entry.rec.part_number, entry.rec.current_location)
        for entry in store._entries.values()
    }
    expected_rate = 0.0
    for pn, location in store.keys:
        if (pn, location) not in keys_with_recommendations:
            continue
        history = store.fs.get_demand_history(
            tenant=store.tenant, pn=pn, location=location
        )
        expected_rate += demand_basis_trace(history).historical_per_day

    for point in forecast.accuracy.points:
        start = date.fromisoformat(point.period_start)
        days = calendar.monthrange(start.year, start.month)[1]
        assert point.projected == pytest.approx(expected_rate * days)


def test_get_forecast_route():
    client, store = _client()
    r = client.get("/v1/tenants/acme/forecast")
    assert r.status_code == 200
    body = r.json()
    assert "service_levels" in body
    assert "method_coverage" in body
    assert "accuracy" in body
    assert body["accuracy"]["status"] == "proxy"
    assert len(body["service_levels"]["bands"]) == 5


def test_forecast_route_unknown_tenant_404():
    client, _ = _client()
    assert client.get("/v1/tenants/ghost/forecast").status_code == 404


def test_forecast_summary_matches_route_payload():
    client, store = _client()
    direct = store.forecast_summary()
    via_route = client.get("/v1/tenants/acme/forecast").json()
    assert via_route == direct.model_dump(mode="json")
