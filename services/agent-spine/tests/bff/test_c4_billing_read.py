from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.bff.billing import BillingSummary
from trax_io_spine.bff.store import PlannerStore

TENANT_UUID = "753b64bd-9885-4639-b116-8f2c5c497232"
_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)
SECRET = "unit-test-secret-0123456789abcdef"


class _V:
    def __init__(self):
        self._v = HsVerifier(SECRET)

    def verify(self, t):
        return self._v.verify(t)


class _FakeRegistry:
    """Duck-typed stand-in for `TenantRegistry` (tenant_registry.py) — the
    billing route's C5 Task 6 fix-round-1 fallback only ever calls
    `uuid_for_slug`, so that single method is all this fake needs to prove
    the fallback fires. A real `TenantRegistry.uuid_for_slug` is already
    proven against real Postgres in tests/pg/test_c5_tenant_registry.py;
    this file's own convention (see the other tests above) is fully
    synthetic stores/readers, so a fake keeps this test in that style."""

    def __init__(self, mapping: dict[str, str]):
        self._mapping = mapping

    def uuid_for_slug(self, slug: str) -> str | None:
        return self._mapping.get(slug)


def _tok(role="planner"):
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "u1",
            "aud": "authenticated",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "tenant_id": TENANT_UUID,
            "tenant_role": role,
        },
        SECRET,
        algorithm="HS256",
    )


def test_billing_endpoint_returns_summary():
    store = PlannerStore.from_extract(
        tenant_id="aeronta-demo", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    summary = BillingSummary(
        plan_tier="growth",
        subscription_status="active",
        key_quota=25000,
        keys_used=42,
        current_period_end=None,
        trial_ends_at=None,
    )
    app = create_planner_app(
        {"aeronta-demo": store},
        verifier=_V(),
        tenant_uuids={"aeronta-demo": TENANT_UUID},
        billing_reader=lambda _uuid: summary,
    )
    r = TestClient(app).get(
        "/v1/tenants/aeronta-demo/billing",
        headers={"Authorization": f"Bearer {_tok('planner')}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["plan_tier"] == "growth" and body["keys_used"] == 42


def test_billing_endpoint_503_when_not_configured():
    store = PlannerStore.from_extract(
        tenant_id="aeronta-demo", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    app = create_planner_app(
        {"aeronta-demo": store}, verifier=_V(), tenant_uuids={"aeronta-demo": TENANT_UUID}
    )
    r = TestClient(app).get(
        "/v1/tenants/aeronta-demo/billing",
        headers={"Authorization": f"Bearer {_tok('planner')}"},
    )
    assert r.status_code == 503


def test_billing_endpoint_never_gated_when_subscription_inactive():
    # Reads are never gated (C4 write-gate only touches writes) — a canceled
    # subscription must still be able to see its own billing status.
    store = PlannerStore.from_extract(
        tenant_id="aeronta-demo", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    summary = BillingSummary(
        plan_tier="growth",
        subscription_status="canceled",
        key_quota=25000,
        keys_used=42,
        current_period_end=None,
        trial_ends_at=None,
    )
    app = create_planner_app(
        {"aeronta-demo": store},
        verifier=_V(),
        tenant_uuids={"aeronta-demo": TENANT_UUID},
        billing_reader=lambda _uuid: summary,
        subscription_status_for=lambda _uuid: "canceled",
    )
    r = TestClient(app).get(
        "/v1/tenants/aeronta-demo/billing",
        headers={"Authorization": f"Bearer {_tok('viewer')}"},
    )
    assert r.status_code == 200


def test_billing_endpoint_404_unknown_tenant():
    store = PlannerStore.from_extract(
        tenant_id="aeronta-demo", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    summary = BillingSummary(
        plan_tier="growth",
        subscription_status="active",
        key_quota=25000,
        keys_used=42,
        current_period_end=None,
        trial_ends_at=None,
    )
    app = create_planner_app(
        {"aeronta-demo": store},
        tenant_uuids={"aeronta-demo": TENANT_UUID},
        billing_reader=lambda _uuid: summary,
    )
    r = TestClient(app).get("/v1/tenants/nope/billing")
    assert r.status_code == 404


def test_billing_endpoint_resolves_via_registry_when_not_prewarmed():
    # C5 Task 6 fix round 1: unlike `_store`/members/ingest, `/billing` had
    # NO registry fallback — every other tenant-scoped surface already fell
    # back through `registry` when the static dict missed, but a brand-new
    # tenant that was never pre-warmed at boot 404'd here (the exact revenue
    # surface — usage meter + Stripe Portal link, plus the over-quota
    # upgrade CTA — the C5 registry work exists to unblock). `tenant_uuids`
    # is deliberately EMPTY below (never pre-warmed); only the fake registry
    # (standing in for a real TenantRegistry backed by Postgres) knows this
    # tenant's uuid. `tenant_uuid_for` mirrors what a real DATABASE_URL boot
    # passes to AuthMiddleware (same registry instance) so the request gets
    # past the tenant-slug match before ever reaching the route handler.
    store = PlannerStore.from_extract(
        tenant_id="aeronta-demo", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    summary = BillingSummary(
        plan_tier="growth",
        subscription_status="active",
        key_quota=25000,
        keys_used=42,
        current_period_end=None,
        trial_ends_at=None,
    )
    registry = _FakeRegistry({"aeronta-demo": TENANT_UUID})
    app = create_planner_app(
        {"aeronta-demo": store},
        verifier=_V(),
        tenant_uuids={},  # never pre-warmed — the exact gap this test closes
        billing_reader=lambda _uuid: summary,
        registry=registry,
        tenant_uuid_for=registry.uuid_for_slug,
    )
    r = TestClient(app).get(
        "/v1/tenants/aeronta-demo/billing",
        headers={"Authorization": f"Bearer {_tok('planner')}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["plan_tier"] == "growth" and body["keys_used"] == 42


def test_billing_endpoint_404_when_reader_raises_value_error():
    # tenant_id IS mapped to a uuid, but the underlying billing_reader (a
    # real pg `billing_summary`) raises ValueError for an unknown/stale
    # tenant uuid — the route must map that to 404, not a raw 500.
    store = PlannerStore.from_extract(
        tenant_id="aeronta-demo", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )

    def _raise(_uuid):
        raise ValueError(f"unknown tenant {_uuid}")

    app = create_planner_app(
        {"aeronta-demo": store},
        verifier=_V(),
        tenant_uuids={"aeronta-demo": TENANT_UUID},
        billing_reader=_raise,
    )
    r = TestClient(app).get(
        "/v1/tenants/aeronta-demo/billing",
        headers={"Authorization": f"Bearer {_tok('planner')}"},
    )
    assert r.status_code == 404
