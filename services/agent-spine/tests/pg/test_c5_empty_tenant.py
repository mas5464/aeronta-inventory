"""A brand-new tenant (no upload yet) must serve clean empty states."""
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.bff.billing import billing_summary
from trax_io_spine.bff.tenant_registry import TenantRegistry
from trax_io_spine.pg.db import tenant_conn

SECRET = "unit-test-secret-0123456789abcdef"


class _V:
    def __init__(self):
        self._v = HsVerifier(SECRET)

    def verify(self, t):
        return self._v.verify(t)


@pytest.fixture()
def empty_client(pg_pool, pg_admin_conn):
    # ON CONFLICT DO UPDATE (not a bare INSERT): this fixture is function-scoped
    # but `_container`/`admin_pool` (conftest.py) are session-scoped — every
    # parametrized case below, plus the separate test at the bottom of this
    # file, runs against the SAME live Postgres, so a second plain INSERT of
    # this hardcoded slug would collide on `tenants_slug_key`. Same idempotent
    # upsert shape pg/seed.py already uses for tenant creation; the tenant
    # stays genuinely empty either way since nothing here ever writes a
    # part_key/recommendation row for it.
    tid = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-empty','Empty') "
        "on conflict (slug) do update set name = excluded.name returning id"
    ).fetchone()[0]
    reg = TenantRegistry(pg_pool)

    # GET /v1/tenants/{tenant}/billing was added to the registry-resolved set
    # during Task 6's review (bff/app.py's `billing` route) — a tenant-scoped
    # read like any other, so it belongs in this surface list too. Mirrors
    # bff/asgi.py's `_billing_reader` wiring exactly (tenant_conn -> RLS sees
    # this tenant's own row).
    def _billing_reader(t_uuid: str):
        with tenant_conn(pg_pool, tenant_uuid=t_uuid) as c:
            return billing_summary(c, t_uuid)

    app = create_planner_app({}, verifier=_V(), registry=reg,
                             tenant_uuid_for=reg.uuid_for_slug,
                             billing_reader=_billing_reader)
    now = datetime.now(UTC)
    tok = jwt.encode(
        {"sub": "u1", "aud": "authenticated", "iat": now, "exp": now + timedelta(minutes=5),
         "tenant_id": str(tid), "tenant_role": "planner"}, SECRET, algorithm="HS256")
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {tok}"})
    return client


# ---------------------------------------------------------------------------
# C5 Task 7 review, fix round 1: the loop below originally asserted only
# `status_code == 200`. FastAPI's response-model validation catches TYPE
# errors (a str where an int belongs) but not VALUE errors — a regression
# that returns a schema-valid but semantically wrong empty body (a phantom
# count, a non-empty band list, a fabricated KPI) would pass a status-only
# check silently. Each predicate below hardcodes the literal expected empty
# state rather than importing pg/store.py's `_EMPTY_*` constants: comparing
# a response against the very constant that produced it would only prove
# the wiring is intact, not that the constant's own values are correct — if
# a future edit fabricated data inside `_EMPTY_DASHBOARD` itself, an
# import-and-compare assertion would still pass. Values below were derived
# by reading each response model (bff/models.py), the empty constants and
# bvr/report.py's + bvr/attribution.py's zero-input behavior, and confirmed
# against FastAPI's actual JSON encoding (Decimal -> float) before being
# written here.
# ---------------------------------------------------------------------------


def _assert_queue_empty(body):
    """`GET /recommendations` — PagedQueue, no rows, first (default) page."""
    assert body == {"items": [], "total": 0, "limit": 50, "offset": 0}


def _assert_dashboard_empty(body):
    """`GET /dashboard` — DashboardSummary, all counts/money zero, every
    breakdown/shortage collection empty (pg/store.py's `_EMPTY_DASHBOARD`)."""
    assert body == {
        "parts": 0,
        "total_on_hand": 0,
        "total_on_hand_value": 0.0,
        "total_shortage": 0.0,
        "total_projected_demand": 0.0,
        "aog_exposure": 0,
        "open_recommendations": 0,
        "net_cost_impact": 0.0,
        "by_criticality": [],
        "by_ata": [],
        "by_part_class": [],
        "by_tier": [],
        "top_shortages": [],
    }


def _assert_forecast_empty(body):
    """`GET /forecast` — ForecastSummary. One `ServiceLevelBand` per configured
    criticality tier still renders (sku_count=0, not a dropped band), method
    coverage and accuracy points are empty, and the accuracy note is an honest
    no-data disclosure, not a fabricated stat (pg/store.py's `_EMPTY_FORECAST`,
    `TenantPolicyConfig().service_level_by_tier` for the per-tier targets)."""
    assert body["service_levels"]["bands"] == [
        {"criticality_tier": 1, "target_service_level": 0.995, "sku_count": 0,
         "actual_coverage": None},
        {"criticality_tier": 2, "target_service_level": 0.98, "sku_count": 0,
         "actual_coverage": None},
        {"criticality_tier": 3, "target_service_level": 0.95, "sku_count": 0,
         "actual_coverage": None},
        {"criticality_tier": 4, "target_service_level": 0.92, "sku_count": 0,
         "actual_coverage": None},
        {"criticality_tier": 5, "target_service_level": 0.9, "sku_count": 0,
         "actual_coverage": None},
    ]
    assert body["method_coverage"] == {"total_skus": 0, "rows": []}
    assert body["accuracy"]["status"] == "proxy"
    assert body["accuracy"]["points"] == []
    assert "No demand history yet" in body["accuracy"]["note"]
    assert "not uploaded any data" in body["accuracy"]["note"]


def _assert_feeds_empty(body):
    """`GET /feeds` — FeedsSummary. The 13-row static feed table (connection
    status is a code-level fact, independent of tenant data — FEED_DEFINITIONS)
    still renders in full; only the per-tenant runtime fields (rows/last_sync/
    extract_date) degrade to null (pg/store.py's `_EMPTY_FEEDS`). Asserting the
    connected/partial/not_connected mix guards against an empty tenant wrongly
    collapsing the table itself (e.g. reporting 0 connected feeds)."""
    assert body["health"] == {
        "connected": 4, "partial": 3, "not_connected": 6, "extract_date": None,
    }
    assert len(body["feeds"]) == 13
    assert all(f["rows"] is None and f["last_sync"] is None for f in body["feeds"])


def _assert_history_empty(body):
    """`GET /history` — [] (Fix 2: pn/location omitted -> [] before the store
    is even reached; see the route comment in bff/app.py)."""
    assert body == []


def _assert_bvr_empty(body):
    """`GET /reports/bvr` — BvrReport v1.1.0, every savings/governance/forward
    figure zeroed, an honest "0/0 tiers at target posture" headline (not "N/A"
    or a dropped field), no seeded extract date.

    Money fields (`Decimal`) are asserted against the literal string "0.00",
    not `0`/`0.0` — confirmed by inspecting the live response body that this
    route's pydantic-model JSON serialization renders `Decimal` as a
    zero-padded string (not a float), so a mistaken `== 0`/`== 0.0` assertion
    here would silently never match and could mask a real regression."""
    period = body["period"]
    assert period["extract_date"] is None
    assert period["decision_window_start"] is None
    assert period["decision_window_end"] is None
    assert period["label"] == "Snapshot (undated)"

    es = body["executive_summary"]
    assert es["total_projected"] == "0.00"
    assert es["changes_applied"] == 0
    assert es["changes_shadowed"] == 0
    assert es["keys_under_management"] == 0
    assert es["open_pipeline_value"] == "0.00"
    assert es["service_headline"] == "0/0 tiers at target posture"

    savings = body["savings"]
    assert savings["total_projected_applied"] == "0.00"
    assert savings["total_projected_shadowed"] == "0.00"
    assert savings["total_projected"] == "0.00"
    assert savings["changes_total"] == 0
    assert savings["changes_valued"] == 0
    for component in ("holding_cost_delta", "ordering_cost_delta", "stockout_risk_delta"):
        assert savings[component]["amount"] == "0.00"

    assert body["service_posture"]["tiers"] == []

    assert body["governance"] == {
        "recommendations_total": 0, "pending": 0, "approved": 0, "rejected": 0,
        "deferred": 0, "approval_rate": 0.0, "override_rate": 0.0,
        "writes_written": 0, "writes_shadowed": 0, "writes_failed": 0,
        "writes_deferred_open_order": 0, "rollbacks": 0,
        "tier_mix": {"A": 0, "B": 0, "C": 0}, "kill_switch_engaged": False,
    }

    fwd = body["forward_look"]
    assert fwd["open_pipeline_value"] == "0.00"
    assert fwd["projected_demand_horizon"] == 0
    assert fwd["top_opportunities"] == []

    meta = body["methodology"]
    assert meta["ledger_entries"] == 0
    assert meta["recommendations"] == 0
    assert meta["keys"] == 0
    assert meta["keys_total_portfolio"] == 0
    assert meta["input_snapshot_hashes"] == []
    assert meta["input_snapshot_hash_count"] == 0


def _assert_billing_empty(body):
    """`GET /billing` — BillingSummary off DB column defaults for a bare
    inserted tenant row (trial tier, 5K quota, never subscribed) and a real
    zero-row `count(*)` over `part_keys` (not a hardcoded 0)."""
    assert body == {
        "plan_tier": "trial",
        "subscription_status": None,
        "key_quota": 5000,
        "keys_used": 0,
        "current_period_end": None,
        "trial_ends_at": None,
    }


_EMPTY_BODY_ASSERTIONS = {
    "/recommendations": _assert_queue_empty,
    "/dashboard": _assert_dashboard_empty,
    "/forecast": _assert_forecast_empty,
    "/feeds": _assert_feeds_empty,
    "/history": _assert_history_empty,
    "/reports/bvr": _assert_bvr_empty,
    "/billing": _assert_billing_empty,
}


@pytest.mark.parametrize("path", list(_EMPTY_BODY_ASSERTIONS))
def test_read_surfaces_serve_empty_state(empty_client, path):
    r = empty_client.get(f"/v1/tenants/c5-empty{path}")
    assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:200]}"
    _EMPTY_BODY_ASSERTIONS[path](r.json())


def test_queue_is_an_empty_page(empty_client):
    body = empty_client.get("/v1/tenants/c5-empty/recommendations").json()
    assert body["items"] == [] and body["total"] == 0
