"""Upload/ingest/poll BFF routes end-to-end with a FakeMinter + real pg + the
real C2 middleware. Slug: acme-c3t5."""
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.pg.uploads import IngestJobStore, UploadMintError

T_UUID = "eeeeeeee-5555-5555-5555-eeeeeeee0c35"
PLANNER = "00000000-0000-0000-0000-00000000c350"
SECRET = "unit-test-secret-0123456789abcdef"


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, 'acme-c3t5', 'A') "
            "on conflict (id) do nothing",
            (T_UUID,),
        )
        conn.commit()


class FakeMinter:
    """`mint(path) -> f"https://signed/{path}"` — the protocol seam standing in
    for `HttpxSignedUrlMinter` so these tests never touch live Storage."""

    def __init__(self):
        self.minted: list[str] = []

    def mint(self, path: str) -> str:
        self.minted.append(path)
        return f"https://signed/{path}"


class BoomMinter:
    def mint(self, path: str) -> str:
        raise UploadMintError("storage unavailable")


def _tok(role: str, *, sub: str = PLANNER, tenant: str = T_UUID, secret: str = SECRET) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": sub, "aud": "authenticated", "iat": now,
            "exp": now + timedelta(minutes=5), "tenant_id": tenant, "tenant_role": role,
        },
        secret, algorithm="HS256",
    )


@pytest.fixture()
def minter():
    return FakeMinter()


@pytest.fixture()
def client(pg_pool, minter):
    app = create_planner_app(
        {},  # planner stores not needed for ingest routes
        verifier=HsVerifier(SECRET),
        tenant_uuids={"acme-c3t5": T_UUID},
        upload_minter=minter,
        ingest_stores={"acme-c3t5": IngestJobStore(pg_pool, tenant_uuid=T_UUID)},
    )
    return TestClient(app)


def test_mint_uploads_as_planner(client):
    h = {"Authorization": f"Bearer {_tok('planner')}"}
    r = client.post(
        "/v1/tenants/acme-c3t5/uploads", headers=h, json={"files": ["parts", "stock"]}
    )
    assert r.status_code == 200
    body = r.json()
    batch_id = body["batch_id"]
    assert set(body["targets"]) == {"parts", "stock"}
    for name, target in body["targets"].items():
        expected_path = f"{T_UUID}/{batch_id}/{name}"
        assert target["path"] == expected_path
        assert target["url"] == f"https://signed/{expected_path}"


def test_mint_uploads_unknown_file_422(client):
    h = {"Authorization": f"Bearer {_tok('planner')}"}
    r = client.post("/v1/tenants/acme-c3t5/uploads", headers=h, json={"files": ["bogus"]})
    assert r.status_code == 422


def test_mint_uploads_viewer_403(client):
    # Middleware write-role floor — the route body never even runs.
    h = {"Authorization": f"Bearer {_tok('viewer')}"}
    r = client.post("/v1/tenants/acme-c3t5/uploads", headers=h, json={"files": ["parts"]})
    assert r.status_code == 403


def test_mint_uploads_no_minter_503(pg_pool):
    app = create_planner_app(
        {},
        verifier=HsVerifier(SECRET),
        tenant_uuids={"acme-c3t5": T_UUID},
        ingest_stores={"acme-c3t5": IngestJobStore(pg_pool, tenant_uuid=T_UUID)},
    )
    c = TestClient(app)
    h = {"Authorization": f"Bearer {_tok('planner')}"}
    r = c.post("/v1/tenants/acme-c3t5/uploads", headers=h, json={"files": ["parts"]})
    assert r.status_code == 503


def test_mint_uploads_storage_failure_502(pg_pool):
    app = create_planner_app(
        {},
        verifier=HsVerifier(SECRET),
        tenant_uuids={"acme-c3t5": T_UUID},
        upload_minter=BoomMinter(),
        ingest_stores={"acme-c3t5": IngestJobStore(pg_pool, tenant_uuid=T_UUID)},
    )
    c = TestClient(app)
    h = {"Authorization": f"Bearer {_tok('planner')}"}
    r = c.post("/v1/tenants/acme-c3t5/uploads", headers=h, json={"files": ["parts"]})
    assert r.status_code == 502


def test_create_ingest_missing_stock_422(client):
    h = {"Authorization": f"Bearer {_tok('planner')}"}
    r = client.post(
        "/v1/tenants/acme-c3t5/ingest", headers=h,
        json={"batch_id": "b1", "files": {"parts": f"{T_UUID}/b1/parts"}},
    )
    assert r.status_code == 422


def test_create_ingest_viewer_403(client):
    h = {"Authorization": f"Bearer {_tok('viewer')}"}
    files = {"parts": f"{T_UUID}/b1/parts", "stock": f"{T_UUID}/b1/stock"}
    r = client.post(
        "/v1/tenants/acme-c3t5/ingest", headers=h, json={"batch_id": "b1", "files": files}
    )
    assert r.status_code == 403


def test_create_ingest_rejects_foreign_tenant_path(client):
    # Cross-tenant exfil guard: a path under a different tenant uuid must be rejected 422
    h = {"Authorization": f"Bearer {_tok('planner')}"}
    foreign = "99999999-9999-9999-9999-999999999999"
    r = client.post(
        "/v1/tenants/acme-c3t5/ingest",
        headers=h,
        json={"batch_id": "b1", "files": {
            "parts": f"{foreign}/b1/parts", "stock": f"{foreign}/b1/stock"}},
    )
    assert r.status_code == 422
    assert "outside tenant prefix" in r.json()["detail"]


def test_create_ingest_rejects_path_traversal(client):
    # Path traversal via .. segments must be rejected even if they start with the tenant uuid
    h = {"Authorization": f"Bearer {_tok('planner')}"}
    foreign = "99999999-9999-9999-9999-999999999999"
    r = client.post(
        "/v1/tenants/acme-c3t5/ingest",
        headers=h,
        json={"batch_id": "b1", "files": {
            "parts": f"{T_UUID}/../{foreign}/b1/parts",
            "stock": f"{T_UUID}/b1/stock"}},
    )
    assert r.status_code == 422
    assert "outside tenant prefix" in r.json()["detail"]


def test_create_ingest_then_poll_and_history(client, admin_pool):
    h = {"Authorization": f"Bearer {_tok('planner')}"}
    files = {"parts": f"{T_UUID}/b1/parts", "stock": f"{T_UUID}/b1/stock"}
    r = client.post(
        "/v1/tenants/acme-c3t5/ingest", headers=h, json={"batch_id": "b1", "files": files}
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    # jobs row exists with the expected kind + payload (uploaded_by = token sub)
    with admin_pool.connection() as conn:
        row = conn.execute(
            "select kind, payload, tenant_id::text from jobs where id = %s", (job_id,)
        ).fetchone()
    assert row is not None
    kind, payload, tenant_id = row
    assert kind == "ingest"
    assert payload["uploaded_by"] == PLANNER
    assert payload["files"] == files
    assert payload["batch_id"] == "b1"
    assert tenant_id == T_UUID

    # poll: freshly-inserted job is queued, no result/errors yet
    r2 = client.get(f"/v1/tenants/acme-c3t5/ingest/{job_id}", headers=h)
    assert r2.status_code == 200
    poll_body = r2.json()
    assert poll_body["status"] == "queued"
    assert poll_body["result"] is None
    assert poll_body["errors"] is None

    # history: the new job shows up
    r3 = client.get("/v1/tenants/acme-c3t5/ingest", headers=h)
    assert r3.status_code == 200
    ids = [j["id"] for j in r3.json()]
    assert job_id in ids


def test_poll_unknown_job_404(client):
    h = {"Authorization": f"Bearer {_tok('planner')}"}
    r = client.get("/v1/tenants/acme-c3t5/ingest/999999999", headers=h)
    assert r.status_code == 404


def test_poll_parses_json_errors(client, admin_pool):
    h = {"Authorization": f"Bearer {_tok('planner')}"}
    files = {"parts": f"{T_UUID}/b2/parts", "stock": f"{T_UUID}/b2/stock"}
    r = client.post(
        "/v1/tenants/acme-c3t5/ingest", headers=h, json={"batch_id": "b2", "files": files}
    )
    job_id = r.json()["job_id"]
    with admin_pool.connection() as conn:
        conn.execute(
            "update jobs set status = 'failed', "
            "error = %s where id = %s",
            ('[{"file": "stock", "message": "bad row"}]', job_id),
        )
        conn.commit()
    r2 = client.get(f"/v1/tenants/acme-c3t5/ingest/{job_id}", headers=h)
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "failed"
    assert body["errors"] == [{"file": "stock", "message": "bad row"}]


def test_ingest_routes_require_auth_no_verifier(pg_pool):
    app = create_planner_app(
        {}, ingest_stores={"acme-c3t5": IngestJobStore(pg_pool, tenant_uuid=T_UUID)}
    )
    c = TestClient(app)
    assert c.get("/v1/tenants/acme-c3t5/ingest").status_code == 401
    assert c.post(
        "/v1/tenants/acme-c3t5/uploads", json={"files": ["parts"]}
    ).status_code == 401
