"""Owner-specific membership RLS: admin manages planner/viewer, only owner touches owner."""
import psycopg
import pytest

from tests.pg.conftest import as_tenant

T = "cccccccc-3333-3333-3333-cccccccc0c3b"
OWNER = "00000000-0000-0000-0000-0000000c3b01"
PLANNER = "00000000-0000-0000-0000-0000000c3b02"


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, 'acme-c3t0b', 'A') "
            "on conflict (id) do nothing",
            (T,),
        )
        conn.execute(
            "insert into memberships (user_id, tenant_id, role) values "
            "(%s, %s, 'owner'), (%s, %s, 'planner') on conflict do nothing",
            (OWNER, T, PLANNER, T),
        )
        conn.commit()


def test_admin_can_manage_planner_row(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, T, role="admin")
        conn.execute(
            "update memberships set role = 'viewer' where user_id = %s and tenant_id = %s",
            (PLANNER, T),
        )
        conn.commit()
    with pg_pool.connection() as conn:  # restore
        as_tenant(conn, T, role="admin")
        conn.execute(
            "update memberships set role = 'planner' where user_id = %s and tenant_id = %s",
            (PLANNER, T),
        )
        conn.commit()


def test_admin_cannot_modify_owner_row(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, T, role="admin")
        # RLS USING excludes the owner row from an admin's UPDATE => 0 rows affected
        cur = conn.execute(
            "update memberships set role = 'planner' where user_id = %s and tenant_id = %s",
            (OWNER, T),
        )
        assert cur.rowcount == 0
        conn.commit()


def test_admin_cannot_promote_to_owner(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, T, role="admin")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "update memberships set role = 'owner' where user_id = %s and tenant_id = %s",
                (PLANNER, T),
            )


def test_owner_can_promote_and_demote(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, T, role="owner")
        conn.execute(
            "update memberships set role = 'owner' where user_id = %s and tenant_id = %s",
            (PLANNER, T),
        )
        conn.execute(
            "update memberships set role = 'planner' where user_id = %s and tenant_id = %s",
            (PLANNER, T),
        )
        conn.commit()
