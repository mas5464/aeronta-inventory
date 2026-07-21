"""MembershipStore semantics on the pg harness + members routes end-to-end with a
FakeAdminApi and the real middleware. Slug: acme-c2t4."""
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.pg.members import (
    AdminApiError,
    HttpxAdminApi,
    LastOwnerError,
    MembershipStore,
)

T_UUID = "cccccccc-cccc-cccc-cccc-cccccccc0c24"
T_UUID2 = "cccccccc-cccc-cccc-cccc-cccccccc0c25"  # second tenant, for cross-tenant preference tests
OWNER = "00000000-0000-0000-0000-00000000c240"
PLANNER = "00000000-0000-0000-0000-00000000c241"


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values "
            "(%s, 'acme-c2t4', 'A'), (%s, 'acme-c2t4-b', 'B') on conflict (id) do nothing",
            (T_UUID, T_UUID2),
        )
        conn.execute(
            "insert into memberships (user_id, tenant_id, role) values "
            "(%s, %s, 'owner'), (%s, %s, 'planner') on conflict do nothing",
            (OWNER, T_UUID, PLANNER, T_UUID),
        )
        conn.commit()


@pytest.fixture()
def store(pg_pool):
    return MembershipStore(pg_pool, tenant_uuid=T_UUID)


def test_list_add_update_remove_as_admin(store):
    new = "00000000-0000-0000-0000-00000000c242"
    store.add(user_id=new, member_role="viewer", role="admin")
    assert any(m["user_id"] == new for m in store.list(role="admin"))
    store.update_role(user_id=new, member_role="planner", role="admin")
    store.remove(user_id=new, role="admin")
    assert all(m["user_id"] != new for m in store.list(role="admin"))


def test_planner_role_cannot_write(store):
    import psycopg

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        store.add(
            user_id="00000000-0000-0000-0000-00000000c243",
            member_role="viewer", role="planner",
        )


def test_last_owner_guard(store):
    with pytest.raises(LastOwnerError):
        store.remove(user_id=OWNER, role="owner")
    with pytest.raises(LastOwnerError):
        store.update_role(user_id=OWNER, member_role="planner", role="owner")


def test_set_preference_upserts_own_row(store, admin_pool):
    # RLS on tenant_preferences gates on the JWT `sub` only — role/tenant_uuid
    # passed to _conn are irrelevant here, only the sub matters (proven by using
    # "planner" — a role with NO membership write grants at all).
    store.set_preference(user_id=PLANNER, target_tenant_uuid=T_UUID, role="planner")
    with admin_pool.connection() as conn:
        row = conn.execute(
            "select tenant_id::text from tenant_preferences where user_id = %s::uuid",
            (PLANNER,),
        ).fetchone()
    assert row is not None and row[0] == T_UUID

    # upsert: calling again with a different target overwrites, not duplicates
    store.set_preference(user_id=PLANNER, target_tenant_uuid=T_UUID2, role="planner")
    with admin_pool.connection() as conn:
        rows = conn.execute(
            "select tenant_id::text from tenant_preferences where user_id = %s::uuid",
            (PLANNER,),
        ).fetchall()
    assert [r[0] for r in rows] == [T_UUID2]


class FakeAdminApi:
    def __init__(self):
        self.invited: list[str] = []

    def invite(self, email):
        self.invited.append(email)
        return f"00000000-0000-0000-0000-0000000{len(self.invited):05d}"

    def emails_for(self, user_ids):
        return {u: f"{u[-4:]}@example.com" for u in user_ids}


def _tok(role, tenant=T_UUID, secret="s3cret"):
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": OWNER, "aud": "authenticated", "iat": now,
         "exp": now + timedelta(minutes=5), "tenant_id": tenant, "tenant_role": role},
        secret, algorithm="HS256",
    )


@pytest.fixture()
def client(pg_pool):
    fake = FakeAdminApi()
    app = create_planner_app(
        {},  # planner stores not needed for members routes
        verifier=HsVerifier("s3cret"),
        tenant_uuids={"acme-c2t4": T_UUID},
        admin_api=fake,
        members_stores={"acme-c2t4": MembershipStore(pg_pool, tenant_uuid=T_UUID)},
    )
    return TestClient(app), fake


def test_members_routes_full_cycle(client):
    c, fake = client
    h_owner = {"Authorization": f"Bearer {_tok('owner')}"}
    h_planner = {"Authorization": f"Bearer {_tok('planner')}"}

    assert c.get("/v1/tenants/acme-c2t4/members", headers=h_planner).status_code == 403
    r = c.get("/v1/tenants/acme-c2t4/members", headers=h_owner)
    assert r.status_code == 200 and any(m["role"] == "owner" for m in r.json())

    r = c.post("/v1/tenants/acme-c2t4/members/invite", headers=h_owner,
               json={"email": "new@acme.test", "role": "planner"})
    assert r.status_code == 200 and fake.invited == ["new@acme.test"]
    uid = r.json()["user_id"]

    assert c.patch(f"/v1/tenants/acme-c2t4/members/{uid}", headers=h_planner,
                   json={"role": "viewer"}).status_code == 403
    assert c.patch(f"/v1/tenants/acme-c2t4/members/{uid}", headers=h_owner,
                   json={"role": "viewer"}).status_code == 200
    assert c.delete(f"/v1/tenants/acme-c2t4/members/{uid}", headers=h_owner).status_code == 200
    assert c.delete(f"/v1/tenants/acme-c2t4/members/{OWNER}", headers=h_owner).status_code == 409


def test_members_routes_require_auth(pg_pool):
    app = create_planner_app({})  # no verifier => members routes must refuse
    c = TestClient(app)
    assert c.get("/v1/tenants/acme-c2t4/members").status_code == 401


def test_invite_duplicate_email_409(client):
    c, fake = client
    h_owner = {"Authorization": f"Bearer {_tok('owner')}"}
    r = c.post("/v1/tenants/acme-c2t4/members/invite", headers=h_owner,
               json={"email": "dup@acme.test", "role": "viewer"})
    assert r.status_code == 200
    uid = r.json()["user_id"]
    # FakeAdminApi.emails_for derives the email from the last 4 chars of the id —
    # re-inviting the SAME already-known email must 409 without minting a new user.
    dup_email = fake.emails_for([uid])[uid]
    before = len(fake.invited)
    r2 = c.post("/v1/tenants/acme-c2t4/members/invite", headers=h_owner,
                json={"email": dup_email, "role": "viewer"})
    assert r2.status_code == 409
    assert len(fake.invited) == before  # no new invite() call happened
    c.delete(f"/v1/tenants/acme-c2t4/members/{uid}", headers=h_owner)  # keep the tenant clean


def test_owner_grant_requires_owner_caller(client):
    c, fake = client
    h_owner = {"Authorization": f"Bearer {_tok('owner')}"}
    h_admin = {"Authorization": f"Bearer {_tok('admin')}"}
    r = c.post("/v1/tenants/acme-c2t4/members/invite", headers=h_owner,
               json={"email": "promote-me@acme.test", "role": "viewer"})
    uid = r.json()["user_id"]
    # an admin caller may not promote to owner ...
    assert c.patch(f"/v1/tenants/acme-c2t4/members/{uid}", headers=h_admin,
                   json={"role": "owner"}).status_code == 403
    # ... but an owner caller may
    assert c.patch(f"/v1/tenants/acme-c2t4/members/{uid}", headers=h_owner,
                   json={"role": "owner"}).status_code == 200
    # now demoting THAT owner also requires an owner caller
    assert c.patch(f"/v1/tenants/acme-c2t4/members/{uid}", headers=h_admin,
                   json={"role": "viewer"}).status_code == 403
    assert c.patch(f"/v1/tenants/acme-c2t4/members/{uid}", headers=h_owner,
                   json={"role": "viewer"}).status_code == 200
    assert c.delete(f"/v1/tenants/acme-c2t4/members/{uid}", headers=h_owner).status_code == 200


def test_httpx_admin_api_error_shape(monkeypatch):
    api = HttpxAdminApi("https://example.invalid", "svc")

    class _R:
        status_code = 500
        text = "boom"

        def json(self):
            return {}

    monkeypatch.setattr("httpx.post", lambda *a, **k: _R())
    with pytest.raises(AdminApiError):
        api.invite("x@y.z")


def test_activate_tenant_requires_claims(client):
    c, _ = client
    r = c.post("/v1/auth/activate-tenant", json={"tenant_id": T_UUID})
    assert r.status_code == 401


def test_activate_tenant_writes_preference(client, admin_pool):
    c, _ = client
    h_planner = {"Authorization": f"Bearer {_tok('planner')}"}
    r = c.post("/v1/auth/activate-tenant", headers=h_planner, json={"tenant_id": T_UUID})
    assert r.status_code == 204
    with admin_pool.connection() as conn:
        row = conn.execute(
            "select tenant_id::text from tenant_preferences where user_id = %s::uuid",
            (OWNER,),  # _tok always mints sub=OWNER regardless of tenant_role claim
        ).fetchone()
    assert row is not None and row[0] == T_UUID


def test_activate_tenant_no_verifier_401(pg_pool):
    app = create_planner_app(
        {}, members_stores={"acme-c2t4": MembershipStore(pg_pool, tenant_uuid=T_UUID)}
    )
    c = TestClient(app)
    assert c.post("/v1/auth/activate-tenant", json={"tenant_id": T_UUID}).status_code == 401
