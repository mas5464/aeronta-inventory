from datetime import UTC, datetime, timedelta

import jwt
from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.bff.whoami import TenantRef, WhoamiResponse

SECRET = "unit-test-secret-0123456789abcdef"
A_UUID = "11111111-1111-1111-1111-111111111111"


class _V:
    def __init__(self):
        self._v = HsVerifier(SECRET)

    def verify(self, t):
        return self._v.verify(t)


def _tok(tenant_uuid=A_UUID, role="owner"):
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": "user-1", "aud": "authenticated", "iat": now,
         "exp": now + timedelta(minutes=5), "tenant_id": tenant_uuid, "tenant_role": role},
        SECRET, algorithm="HS256")


def _client(reader):
    return TestClient(create_planner_app({}, verifier=_V(), whoami_reader=reader))


def test_returns_active_and_list():
    ref = TenantRef(tenant_uuid=A_UUID, slug="acme", name="Acme", role="owner")

    def reader(sub, active):
        return WhoamiResponse(user_id=sub, active=ref, tenants=[ref])

    r = _client(reader).get("/v1/auth/whoami",
                            headers={"Authorization": f"Bearer {_tok()}"})
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "user-1"
    assert body["active"]["slug"] == "acme"
    assert [t["slug"] for t in body["tenants"]] == ["acme"]


def test_no_memberships_is_200_with_nulls():
    def reader(sub, active):
        return WhoamiResponse(user_id=sub, active=None, tenants=[])

    r = _client(reader).get("/v1/auth/whoami",
                            headers={"Authorization": f"Bearer {_tok()}"})
    assert r.status_code == 200
    assert r.json()["active"] is None and r.json()["tenants"] == []


def test_unauthenticated_is_401():
    def reader(sub, active):
        return WhoamiResponse(user_id=sub, active=None, tenants=[])

    assert _client(reader).get("/v1/auth/whoami").status_code == 401


def test_unconfigured_reader_is_503():
    assert TestClient(create_planner_app({}, verifier=_V())).get(
        "/v1/auth/whoami", headers={"Authorization": f"Bearer {_tok()}"}
    ).status_code == 503
