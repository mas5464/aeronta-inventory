"""One app instance serves any tenant in the database, with isolation intact.

C5 Task 6: `_store` / members / ingest all resolve through a shared
`TenantRegistry` on demand, and `PLANNER_TENANT` becomes a pre-warm hint
rather than a requirement. Three corrections govern over the original plan
here (decided during review of earlier C5 tasks):

1. `any_members_store()` was deleted in Task 3's review (its cold-cache
   fallback ran an RLS-blocked bare-pool read) — `activate_tenant` uses
   `registry.members_store_for_uuid(claims["tenant_id"])` instead.
2. `/healthz` must not disclose tenant slugs to an unauthenticated caller
   once serving is dynamic — see `test_healthz_does_not_disclose_tenant_slugs_anonymously`.
3. `activate_tenant` stays exempt from the tenant-slug match (by design), but
   must still only ever write the CALLER's own preference, keyed on their
   verified JWT `sub` — re-verified below now that its store is
   registry-backed.
"""
from datetime import UTC, datetime, timedelta

import jwt
from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.bff.tenant_registry import TenantRegistry

SECRET = "unit-test-secret-0123456789abcdef"


class _V:
    def __init__(self):
        self._v = HsVerifier(SECRET)

    def verify(self, t):
        return self._v.verify(t)


def _tok(tenant_uuid, role="planner", sub="u1"):
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": sub, "aud": "authenticated", "iat": now, "exp": now + timedelta(minutes=5),
         "tenant_id": str(tenant_uuid), "tenant_role": role},
        SECRET, algorithm="HS256")


def test_one_app_serves_two_tenants_and_blocks_crossover(pg_pool, pg_admin_conn):
    a = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-serve-a','A') returning id").fetchone()[0]
    b = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-serve-b','B') returning id").fetchone()[0]
    reg = TenantRegistry(pg_pool)
    app = create_planner_app({}, verifier=_V(), registry=reg,
                             tenant_uuid_for=reg.uuid_for_slug)
    c = TestClient(app)

    # Each tenant is served with its own token — neither was configured at boot.
    assert c.get("/v1/tenants/c5-serve-a/recommendations",
                 headers={"Authorization": f"Bearer {_tok(a)}"}).status_code == 200
    assert c.get("/v1/tenants/c5-serve-b/recommendations",
                 headers={"Authorization": f"Bearer {_tok(b)}"}).status_code == 200
    # Crossover is refused.
    assert c.get("/v1/tenants/c5-serve-b/recommendations",
                 headers={"Authorization": f"Bearer {_tok(a)}"}).status_code == 403


# ---- Correction 1: any_members_store() was deleted in Task 3's review;
# activate_tenant must resolve via registry.members_store_for_uuid(...) when
# no static members_stores dict is configured. ----


def test_activate_tenant_falls_back_to_members_store_for_uuid(pg_pool, pg_admin_conn):
    caller_sub = "00000000-0000-0000-0000-0000000c5a01"
    caller_tenant = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-activate-caller','Caller') "
        "returning id"
    ).fetchone()[0]
    target_tenant = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-activate-target','Target') "
        "returning id"
    ).fetchone()[0]
    reg = TenantRegistry(pg_pool)
    # Deliberately NO members_stores= — the registry-only boot this task adds
    # (a fresh deployment with no PLANNER_TENANT pre-warm).
    app = create_planner_app({}, verifier=_V(), registry=reg,
                             tenant_uuid_for=reg.uuid_for_slug)
    c = TestClient(app)
    h = {"Authorization": f"Bearer {_tok(caller_tenant, sub=caller_sub)}"}
    r = c.post("/v1/auth/activate-tenant", headers=h, json={"tenant_id": str(target_tenant)})
    assert r.status_code == 204
    row = pg_admin_conn.execute(
        "select tenant_id::text from tenant_preferences where user_id = %s::uuid",
        (caller_sub,),
    ).fetchone()
    assert row is not None and row[0] == str(target_tenant)


# ---- Correction 3: activate_tenant is deliberately exempt from the
# tenant-slug match (it's how a caller switches tenants) — re-verify it still
# writes ONLY the caller's own preference, keyed on their verified JWT `sub`,
# now that its store is registry-backed. ----


def test_activate_tenant_writes_only_the_callers_own_sub(pg_pool, pg_admin_conn):
    sub_1 = "00000000-0000-0000-0000-0000000c5a02"
    sub_2 = "00000000-0000-0000-0000-0000000c5a03"
    caller_tenant = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-activate-caller2','Caller2') "
        "returning id"
    ).fetchone()[0]
    target_tenant = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-activate-target2','Target2') "
        "returning id"
    ).fetchone()[0]
    reg = TenantRegistry(pg_pool)
    app = create_planner_app({}, verifier=_V(), registry=reg,
                             tenant_uuid_for=reg.uuid_for_slug)
    c = TestClient(app)
    # Two different callers, same target tenant, same members store instance
    # underneath (registry-cached) — each write must land under its OWN sub.
    for sub in (sub_1, sub_2):
        h = {"Authorization": f"Bearer {_tok(caller_tenant, sub=sub)}"}
        r = c.post("/v1/auth/activate-tenant", headers=h, json={"tenant_id": str(target_tenant)})
        assert r.status_code == 204
    rows = {
        row[0]: row[1]
        for row in pg_admin_conn.execute(
            "select user_id::text, tenant_id::text from tenant_preferences "
            "where user_id in (%s::uuid, %s::uuid)",
            (sub_1, sub_2),
        ).fetchall()
    }
    assert rows == {sub_1: str(target_tenant), sub_2: str(target_tenant)}


# ---- Correction 2: /healthz must not disclose tenant slugs to an
# unauthenticated caller once _store is registry-backed. ----


def test_healthz_does_not_disclose_tenant_slugs_anonymously(pg_pool, pg_admin_conn):
    secret_slug = "c5-secret-slug"
    pg_admin_conn.execute(
        "insert into tenants (slug, name) values (%s, 'Secret')", (secret_slug,)
    )
    reg = TenantRegistry(pg_pool)
    # Warm the registry's cache for this slug — exactly the state that WOULD
    # leak if healthz still returned `registry.known_slugs()` (the plan's
    # original design, superseded by this task's review correction).
    assert reg.uuid_for_slug(secret_slug) is not None
    app = create_planner_app({}, verifier=_V(), registry=reg,
                             tenant_uuid_for=reg.uuid_for_slug)
    c = TestClient(app)
    r = c.get("/healthz")  # deliberately no Authorization header at all
    assert r.status_code == 200
    assert secret_slug not in r.text


# ---- ingest_routes.py "tenant lookups" (named in this task's own Files
# list) must also resolve through the registry — otherwise the self-serve
# upload flow (C3) stays single-tenant even after this task. ----


def test_ingest_routes_resolve_via_registry_when_not_prewarmed(pg_pool, pg_admin_conn):
    slug = "c5-serve-ingest"
    tid = str(pg_admin_conn.execute(
        "insert into tenants (slug,name) values (%s,'Ingest') returning id", (slug,)
    ).fetchone()[0])
    reg = TenantRegistry(pg_pool)
    app = create_planner_app({}, verifier=_V(), registry=reg,
                             tenant_uuid_for=reg.uuid_for_slug)
    c = TestClient(app)
    h = {"Authorization": f"Bearer {_tok(tid)}"}
    files = {"parts": f"{tid}/b1/parts", "stock": f"{tid}/b1/stock"}
    r = c.post(f"/v1/tenants/{slug}/ingest", headers=h, json={"batch_id": "b1", "files": files})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    assert c.get(f"/v1/tenants/{slug}/ingest/{job_id}", headers=h).status_code == 200


# ---- PLANNER_TENANT becomes a pre-warm hint, not a requirement, in
# DATABASE_URL mode: the BFF must boot with it unset. ----


def test_build_app_boots_with_planner_tenant_unset(_container, pg_admin_conn, monkeypatch):
    slug = "c5-serve-unwarmed"
    sub = "00000000-0000-0000-0000-0000000c5a04"
    tid = pg_admin_conn.execute(
        "insert into tenants (slug, name) values (%s, 'Unwarmed') returning id", (slug,)
    ).fetchone()[0]
    pg_admin_conn.execute(
        "insert into memberships (user_id, tenant_id, role) values (%s::uuid, %s, 'owner')",
        (sub, tid),
    )

    url = _container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    app_url = url.replace(_container.username, "trax_app", 1).replace(
        _container.password, "trax_app", 1
    )
    monkeypatch.setenv("DATABASE_URL", app_url)
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)
    monkeypatch.delenv("AUTH_JWKS_URL", raising=False)
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("PLANNER_TENANT", raising=False)

    from trax_io_spine.bff.asgi import build_app

    app = build_app()  # must NOT raise with PLANNER_TENANT unset
    c = TestClient(app)
    h = {"Authorization": f"Bearer {_tok(tid, sub=sub)}"}
    r = c.get(f"/v1/tenants/{slug}/recommendations", headers=h)
    assert r.status_code == 200

    # whoami's own reader closure falls back to this boot's `tenant_uuid`
    # (now possibly None) only when the caller's OWN active-tenant claim is
    # absent — here it is present, so this also proves that fallback being
    # None doesn't break the common case.
    who = c.get("/v1/auth/whoami", headers=h)
    assert who.status_code == 200
    body = who.json()
    assert body["active"]["slug"] == slug
    assert any(t["slug"] == slug for t in body["tenants"])


# ---- Fix round 1, Fix 3: asgi.py's pre-warm must build members_stores/
# ingest_stores through the SAME `registry`, not a second, independent
# construction of the same kind of object. ----


def test_asgi_prewarms_members_and_ingest_stores_via_registry(
    _container, pg_admin_conn, monkeypatch
):
    """Before this fix, asgi.py's DATABASE_URL pre-warm block built
    `MembershipStore(pool, tenant_uuid=...)`/`IngestJobStore(pool,
    tenant_uuid=...)` directly — a second, independent construction of
    exactly the object `registry.members_store_for`/`.ingest_store_for`
    would hand out to any later, not-pre-warmed caller for the SAME tenant.
    Both objects are stateless wrappers over the same pool/tenant_uuid, so
    this was never a correctness bug, but it meant three different
    construction paths for what should be one. This proves the fix by
    IDENTITY (not just "does it still work"): the pre-warmed dict entries
    must be the literal same objects `registry` resolves later, and the
    pre-warmed tenant's members route must still work end-to-end.
    """
    slug = "c5-serve-prewarmed"
    sub = "00000000-0000-0000-0000-0000000c5a06"
    tid = pg_admin_conn.execute(
        "insert into tenants (slug, name) values (%s, 'Prewarmed') returning id", (slug,)
    ).fetchone()[0]
    pg_admin_conn.execute(
        "insert into memberships (user_id, tenant_id, role) values (%s::uuid, %s, 'owner')",
        (sub, tid),
    )

    url = _container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    app_url = url.replace(_container.username, "trax_app", 1).replace(
        _container.password, "trax_app", 1
    )
    monkeypatch.setenv("DATABASE_URL", app_url)
    monkeypatch.setenv("AUTH_JWT_SECRET", SECRET)
    monkeypatch.delenv("AUTH_JWKS_URL", raising=False)
    monkeypatch.delenv("AUTH_DEV_MODE", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setenv("PLANNER_TENANT", slug)  # resolves this time — hits the pre-warm branch

    from trax_io_spine.bff.asgi import build_app

    app = build_app()
    registry = app.state.registry
    assert app.state.members_stores[slug] is registry.members_store_for(slug)
    assert app.state.ingest_stores[slug] is registry.ingest_store_for(slug)

    c = TestClient(app)
    h = {"Authorization": f"Bearer {_tok(tid, role='owner', sub=sub)}"}
    r = c.get(f"/v1/tenants/{slug}/members", headers=h)
    assert r.status_code == 200
