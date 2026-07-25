"""TenantRegistry resolves real tenants on demand against real Postgres."""
import uuid

from trax_io_spine.bff.tenant_registry import TenantRegistry


def test_resolves_and_caches_a_real_tenant(pg_pool, pg_admin_conn):
    tid = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-reg','Reg') returning id").fetchone()[0]
    reg = TenantRegistry(pg_pool)
    assert reg.uuid_for_slug("c5-reg") == str(tid)
    store = reg.store_for("c5-reg")
    assert store is not None and store.tenant_id == "c5-reg"
    # Same object on a second call — cached, not rebuilt.
    assert reg.store_for("c5-reg") is store


def test_unknown_slug_returns_none_and_is_not_cached(pg_pool, pg_admin_conn):
    reg = TenantRegistry(pg_pool)
    assert reg.uuid_for_slug("c5-later") is None
    assert reg.store_for("c5-later") is None
    # A tenant created AFTER the miss must be reachable immediately: misses
    # are deliberately never cached.
    pg_admin_conn.execute("insert into tenants (slug,name) values ('c5-later','Later')")
    assert reg.uuid_for_slug("c5-later") is not None
    assert reg.store_for("c5-later") is not None


def test_members_and_ingest_stores_resolve(pg_pool, pg_admin_conn):
    pg_admin_conn.execute("insert into tenants (slug,name) values ('c5-mi','MI')")
    reg = TenantRegistry(pg_pool)
    assert reg.members_store_for("c5-mi") is not None
    assert reg.ingest_store_for("c5-mi") is not None
    assert "c5-mi" in reg.known_slugs()


def test_members_store_for_uuid_cold_writes_for_real_as_trax_app(pg_pool, pg_admin_conn):
    """Regression for the defect fixed in `any_members_store()`'s removal: that
    method's cold-cache fallback ran `select slug from tenants limit 1` on a
    BARE pool connection. `tenants` is RLS-protected (`tenants_select`, keyed
    on `current_tenant_id()`) and `pg_pool` connects as the real `trax_app`
    role — `nobypassrls` (tests/pg/auth_shim.sql), matching production
    exactly — so that bare query returned zero rows no matter how many
    tenants existed, and `any_members_store()` returned `None` on every cold
    start. Note this test uses `pg_pool`, NOT a superuser connection: a
    superuser-backed pool would pass this test even if RLS were completely
    broken, which is exactly how the original bug survived review once
    already (see .claude/memory/lessons.md, "Bare pool reads on RLS'd tables
    silently return zero rows as trax_app").

    `members_store_for_uuid` replaces `any_members_store()` entirely: no
    slug lookup, no bare query, no cache to warm first — it just binds a
    `MembershipStore` to a uuid the caller already holds. This test exercises
    it stone cold (a fresh registry, no prior `members_store_for` call for
    this tenant at all) and proves the returned store actually WORKS against
    real Postgres: `set_preference`'s insert runs through `MembershipStore`'s
    own `tenant_conn` usage, so if the store (or anything upstream of it)
    ever regressed to a bare, claims-less connection, `tenant_preferences`'s
    RLS `with check` would reject the insert and this test would fail for
    real, not pass by superuser accident."""
    tid = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-uuid','UuidDirect') returning id"
    ).fetchone()[0]
    reg = TenantRegistry(pg_pool)  # fresh — zero slug-keyed cache warmth

    store = reg.members_store_for_uuid(str(tid))
    assert store is not None
    # Same object on a second call — cached by uuid, not rebuilt.
    assert reg.members_store_for_uuid(str(tid)) is store
    # The uuid-keyed cache is independent of the slug-keyed one: resolving by
    # slug for the same tenant must not return the same cached object.
    assert reg.members_store_for("c5-uuid") is not store

    user_id = str(uuid.uuid4())
    store.set_preference(user_id=user_id, target_tenant_uuid=str(tid), role="planner")

    row = pg_admin_conn.execute(
        "select tenant_id::text from tenant_preferences where user_id = %s::uuid", (user_id,)
    ).fetchone()
    assert row is not None and row[0] == str(tid)
