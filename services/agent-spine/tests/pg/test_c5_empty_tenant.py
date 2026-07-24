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


@pytest.mark.parametrize("path", [
    "/recommendations",
    "/dashboard",
    "/forecast",
    "/feeds",
    "/history",
    "/reports/bvr",
    "/billing",
])
def test_read_surfaces_serve_empty_state(empty_client, path):
    r = empty_client.get(f"/v1/tenants/c5-empty{path}")
    assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:200]}"


def test_queue_is_an_empty_page(empty_client):
    body = empty_client.get("/v1/tenants/c5-empty/recommendations").json()
    assert body["items"] == [] and body["total"] == 0
