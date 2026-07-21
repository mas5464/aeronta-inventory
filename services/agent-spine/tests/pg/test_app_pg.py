"""The existing FastAPI contract served off PgPlannerStore — key routes only
(the exhaustive route behavior suite is tests/bff/test_app.py; parity of the
store beneath it is Tasks 9-12. This pins the duck-typing seam end-to-end).

Tenant slug is "acme-t13" (not "acme") — admin_pool/pg_pool are session-scoped
against one shared Postgres container across tests/pg/*, so every task uses its
own slug to avoid fixed-slug collisions (see tests/pg/test_pg_store_actions.py
etc. for the same convention: acme-t10, acme-t11, ...).
"""
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
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
