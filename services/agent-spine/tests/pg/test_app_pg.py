"""The existing FastAPI contract served off PgPlannerStore — key routes only
(the exhaustive route behavior suite is tests/bff/test_app.py; parity of the
store beneath it is Tasks 9-12. This pins the duck-typing seam end-to-end).

Tenant slug is "acme-t13" (not "acme") — admin_pool/pg_pool are session-scoped
against one shared Postgres container across tests/pg/*, so every task uses its
own slug to avoid fixed-slug collisions (see tests/pg/test_pg_store_actions.py
etc. for the same convention: acme-t10, acme-t11, ...).
"""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.bff.store import PlannerStore
from trax_io_spine.pg.seed import seed_store
from trax_io_spine.pg.store import PgPlannerStore

EXTRACT = (
    Path(__file__).resolve().parents[3]
    / "recommendation-engine" / "examples" / "extract_sample"
)
TENANT = "acme-t13"


@pytest.fixture()
def client(admin_pool, pg_pool):
    mem = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    report = seed_store(admin_pool, store=mem, slug=TENANT, name="Acme Air")
    store = PgPlannerStore(pg_pool, tenant_slug=TENANT, tenant_uuid=report.tenant_uuid)
    return TestClient(create_planner_app({TENANT: store}))


def test_queue_and_detail(client):
    rows = client.get(f"/v1/tenants/{TENANT}/recommendations").json()["items"]
    assert rows and client.get(
        f"/v1/tenants/{TENANT}/recommendations/{rows[0]['recommendation_id']}"
    ).status_code == 200


def test_approve_flow_and_history(client):
    rows = client.get(f"/v1/tenants/{TENANT}/recommendations").json()["items"]
    row = next(r for r in rows if r["approvable"])
    r = client.post(f"/v1/tenants/{TENANT}/recommendations/{row['recommendation_id']}/approve")
    assert r.status_code == 200
    h = client.get(
        f"/v1/tenants/{TENANT}/history", params={"pn": row["pn"], "location": row["location"]}
    )
    assert h.status_code == 200 and h.json()


def test_kill_switch_423(client):
    assert client.post(
        f"/v1/tenants/{TENANT}/killswitch", json={"engaged": True}
    ).status_code == 200
    rows = client.get(f"/v1/tenants/{TENANT}/recommendations").json()["items"]
    rid = next(r["recommendation_id"] for r in rows if r["approvable"])
    assert client.post(
        f"/v1/tenants/{TENANT}/recommendations/{rid}/approve"
    ).status_code == 423


def test_dashboard_bvr_unknown_tenant(client):
    assert client.get(f"/v1/tenants/{TENANT}/dashboard").status_code == 200
    assert client.get(f"/v1/tenants/{TENANT}/reports/bvr").status_code == 200
    assert client.get("/v1/tenants/ghost/dashboard").status_code == 404


# ---- C3 Task 0a: authed-approve attributes the verified caller as principal ----

_AUTH_SECRET = "unit-test-secret-0123456789abcdef"
AUTHED_TENANT = "acme-c3t0a-app"


def _token(tenant_uuid: str, *, sub: str = "user-live-1", role: str = "planner") -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": sub, "aud": "authenticated", "iat": now, "exp": now + timedelta(minutes=5),
         "tenant_id": tenant_uuid, "tenant_role": role},
        _AUTH_SECRET, algorithm="HS256",
    )


@pytest.fixture()
def authed_client(admin_pool, pg_pool):
    mem = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    report = seed_store(admin_pool, store=mem, slug=AUTHED_TENANT, name="Acme Air")
    store = PgPlannerStore(pg_pool, tenant_slug=AUTHED_TENANT, tenant_uuid=report.tenant_uuid)
    app = create_planner_app(
        {AUTHED_TENANT: store},
        verifier=HsVerifier(_AUTH_SECRET),
        tenant_uuids={AUTHED_TENANT: report.tenant_uuid},
    )
    return TestClient(app), report


def test_authed_approve_attributes_verified_caller_as_principal(authed_client, admin_pool):
    client, report = authed_client
    auth = {"Authorization": f"Bearer {_token(report.tenant_uuid)}"}
    rows = client.get(f"/v1/tenants/{AUTHED_TENANT}/recommendations", headers=auth).json()[
        "items"
    ]
    row = next(r for r in rows if r["approvable"])
    r = client.post(
        f"/v1/tenants/{AUTHED_TENANT}/recommendations/{row['recommendation_id']}/approve",
        headers=auth,
    )
    assert r.status_code == 200
    with admin_pool.connection() as conn:
        decision = conn.execute(
            "select principal from decisions "
            "where tenant_id = %s::uuid and action = 'approve'",
            (report.tenant_uuid,),
        ).fetchone()
        ledger = conn.execute(
            "select entry->>'changed_by_principal' from writeback_ledger "
            "where tenant_id = %s::uuid order by version desc limit 1",
            (report.tenant_uuid,),
        ).fetchone()
    assert decision[0] == "user-live-1"
    assert ledger[0] == "user-live-1"
