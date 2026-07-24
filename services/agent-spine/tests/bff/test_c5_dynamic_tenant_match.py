"""Dynamic tenant resolution must not weaken the tenant-match assertion."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.bff.store import PlannerStore

SECRET = "unit-test-secret-0123456789abcdef"
A_UUID = "11111111-1111-1111-1111-111111111111"
B_UUID = "22222222-2222-2222-2222-222222222222"
_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


class _V:
    def __init__(self):
        self._v = HsVerifier(SECRET)

    def verify(self, t):
        return self._v.verify(t)


def _tok(tenant_uuid: str, role: str = "planner") -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": "u1", "aud": "authenticated", "iat": now, "exp": now + timedelta(minutes=5),
         "tenant_id": tenant_uuid, "tenant_role": role},
        SECRET, algorithm="HS256")


def _client():
    """Two tenants resolvable dynamically; only tenant-a has a store configured
    (mirrors Task 6's registry, where any real slug resolves)."""
    store = PlannerStore.from_extract(
        tenant_id="tenant-a", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC))
    resolver = {"tenant-a": A_UUID, "tenant-b": B_UUID}.get
    app = create_planner_app(
        {"tenant-a": store}, verifier=_V(), tenant_uuid_for=resolver)
    return TestClient(app)


def test_matching_claim_is_served():
    r = _client().get("/v1/tenants/tenant-a/recommendations",
                      headers={"Authorization": f"Bearer {_tok(A_UUID)}"})
    assert r.status_code == 200


def test_cross_tenant_token_is_rejected_not_served():
    """THE regression guard: a tenant-B token addressing tenant-a must 403.
    Before C5 the match was skipped whenever the slug wasn't in the static
    dict, which would serve tenant-a's data to a tenant-b caller."""
    r = _client().get("/v1/tenants/tenant-a/recommendations",
                      headers={"Authorization": f"Bearer {_tok(B_UUID)}"})
    assert r.status_code == 403


def test_unresolvable_slug_is_403_not_a_fallthrough():
    r = _client().get("/v1/tenants/does-not-exist/recommendations",
                      headers={"Authorization": f"Bearer {_tok(A_UUID)}"})
    assert r.status_code == 403


def test_static_dict_path_still_works_without_resolver():
    store = PlannerStore.from_extract(
        tenant_id="tenant-a", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC))
    app = create_planner_app({"tenant-a": store}, verifier=_V(),
                             tenant_uuids={"tenant-a": A_UUID})
    r = TestClient(app).get("/v1/tenants/tenant-a/recommendations",
                            headers={"Authorization": f"Bearer {_tok(A_UUID)}"})
    assert r.status_code == 200


def test_resolver_failure_is_503_not_a_raw_500():
    """A DB blip during resolution must fail closed with a clean, retryable
    503 — never an unhandled exception, and never a fallthrough that would
    serve unverified data. Same posture as the C4 subscription gate."""
    def _boom(_slug):
        raise RuntimeError("pool exhausted")

    store = PlannerStore.from_extract(
        tenant_id="tenant-a", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC))
    app = create_planner_app({"tenant-a": store}, verifier=_V(), tenant_uuid_for=_boom)
    r = TestClient(app).get("/v1/tenants/tenant-a/recommendations",
                            headers={"Authorization": f"Bearer {_tok(A_UUID)}"})
    assert r.status_code == 503
