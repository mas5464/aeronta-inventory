"""Two-tenant isolation for the tenancy core (the 4-layer convention, data layer)."""
import pytest

from tests.pg.conftest import as_tenant

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture(scope="module", autouse=True)
def two_tenants(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values "
            "(%s, 'acme', 'Acme Air'), (%s, 'globex', 'Globex Airways') "
            "on conflict (id) do nothing",
            (A, B),
        )
        conn.execute(
            "insert into memberships (user_id, tenant_id, role) values "
            "('00000000-0000-0000-0000-0000000000aa', %s, 'owner'), "
            "('00000000-0000-0000-0000-0000000000bb', %s, 'owner') "
            "on conflict do nothing",
            (A, B),
        )
        conn.commit()


def test_member_sees_only_own_tenant(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        slugs = [r[0] for r in conn.execute("select slug from tenants").fetchall()]
        assert slugs == ["acme"]


def test_no_claims_sees_nothing(pg_pool):
    with pg_pool.connection() as conn:
        assert conn.execute("select count(*) from tenants").fetchone()[0] == 0


def test_memberships_scoped(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, B)
        rows = conn.execute("select tenant_id::text from memberships").fetchall()
        assert {r[0] for r in rows} == {B}


def test_app_role_cannot_insert_tenants(pg_pool):
    import psycopg

    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("insert into tenants (slug, name) values ('evil', 'Evil')")
