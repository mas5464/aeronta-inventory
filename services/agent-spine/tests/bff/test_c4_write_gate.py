from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
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


def _client(status):
    store = PlannerStore.from_extract(
        tenant_id="aeronta-demo", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    app = create_planner_app(
        {"aeronta-demo": store},
        verifier=_V(),
        tenant_uuids={"aeronta-demo": TENANT_UUID},
        subscription_status_for=lambda _uuid: status,
    )
    return TestClient(app)


@pytest.mark.parametrize(
    "status,code",
    [
        ("trialing", 404),
        ("active", 404),
        ("past_due", 404),  # write reaches handler (404 unknown rec)
        ("canceled", 402),
        ("unpaid", 402),
        ("paused", 402),
        ("incomplete", 402),
        (None, 402),
    ],
)
def test_write_gate_matrix(status, code):
    r = _client(status).post(
        "/v1/tenants/aeronta-demo/recommendations/nope/approve",
        headers={"Authorization": f"Bearer {_tok('planner')}"},
    )
    assert r.status_code == code


def test_reads_never_gated_even_when_canceled():
    r = _client("canceled").get(
        "/v1/tenants/aeronta-demo/recommendations",
        headers={"Authorization": f"Bearer {_tok('viewer')}"},
    )
    assert r.status_code == 200


def test_no_gate_callable_means_no_gating():
    # Omitting subscription_status_for keeps the in-memory/dev behavior.
    store = PlannerStore.from_extract(
        tenant_id="aeronta-demo", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    app = create_planner_app(
        {"aeronta-demo": store}, verifier=_V(), tenant_uuids={"aeronta-demo": TENANT_UUID}
    )
    r = TestClient(app).post(
        "/v1/tenants/aeronta-demo/recommendations/nope/approve",
        headers={"Authorization": f"Bearer {_tok('planner')}"},
    )
    assert r.status_code == 404  # reaches handler, not 402
