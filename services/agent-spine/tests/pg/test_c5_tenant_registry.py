"""TenantRegistry resolves real tenants on demand against real Postgres."""
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
    assert reg.any_members_store() is not None
    assert "c5-mi" in reg.known_slugs()
