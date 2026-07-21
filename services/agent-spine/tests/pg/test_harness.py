"""The harness itself is load-bearing: auth shim functions + roles must exist."""
from tests.pg.conftest import as_tenant


def test_auth_shim_jwt_roundtrip(admin_pool):
    with admin_pool.connection() as conn:
        as_tenant(conn, "11111111-1111-1111-1111-111111111111", role="admin")
        row = conn.execute(
            "select auth.jwt()->>'tenant_id', auth.jwt()->>'tenant_role'"
        ).fetchone()
        assert row == ("11111111-1111-1111-1111-111111111111", "admin")


def test_jwt_empty_outside_transaction_claims(admin_pool):
    with admin_pool.connection() as conn:
        assert conn.execute("select auth.jwt()").fetchone()[0] == {}


def test_app_role_cannot_bypass_rls(pg_pool):
    with pg_pool.connection() as conn:
        row = conn.execute(
            "select rolbypassrls from pg_roles where rolname = current_user"
        ).fetchone()
        assert row[0] is False
