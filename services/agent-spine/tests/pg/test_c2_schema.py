"""Migration 0006 semantics: slug resolve as trax_app, memberships write gates,
jobs isolation. Slugs here: acme-c2t1 / globex-c2t1 (session isolation convention)."""
import json

import psycopg
import pytest

from tests.pg.conftest import as_tenant

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0c21"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0c21"
U_NEW = "00000000-0000-0000-0000-00000000c210"


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values "
            "(%s, 'acme-c2t1', 'A'), (%s, 'globex-c2t1', 'B') on conflict (id) do nothing",
            (A, B),
        )
        conn.commit()


def _claims(conn, tenant, role):
    as_tenant(conn, tenant, role=role)


def test_resolve_tenant_slug_as_trax_app_without_claims(pg_pool):
    with pg_pool.connection() as conn:
        row = conn.execute("select public.resolve_tenant_slug('acme-c2t1')::text").fetchone()
        assert row[0] == A
        assert conn.execute(
            "select public.resolve_tenant_slug('nope-c2t1')"
        ).fetchone()[0] is None


def test_resolve_tenant_slug_denied_to_authenticated_role(pg_admin_conn):
    """C5 final review, Group D bonus (migration
    20260725000015_resolve_tenant_slug_privileges.sql): `resolve_tenant_slug`
    is `security definer` and bypasses RLS to resolve ANY tenant's slug to
    its uuid — the same default-privilege exposure `enqueue_due_recomputes()`
    had (Supabase's platform baseline grants EXECUTE on every public-schema
    function to `authenticated` by default; `revoke all ... from public`
    does not touch that). Must be denied to a PostgREST-style `authenticated`
    caller, unlike `trax_app` above, which keeps its own explicit grant."""
    pg_admin_conn.execute("set role authenticated")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg_admin_conn.execute("select public.resolve_tenant_slug('acme-c2t1')")
    pg_admin_conn.execute("reset role")


def test_admin_can_insert_membership_planner_cannot(pg_pool):
    with pg_pool.connection() as conn:
        _claims(conn, A, "admin")
        conn.execute(
            "insert into memberships (user_id, tenant_id, role) values (%s, %s, 'planner')",
            (U_NEW, A),
        )
        conn.commit()
    with pg_pool.connection() as conn:
        _claims(conn, A, "planner")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "insert into memberships (user_id, tenant_id, role) values "
                "('00000000-0000-0000-0000-00000000c211', %s, 'viewer')",
                (A,),
            )


def test_admin_cannot_write_foreign_tenant_membership(pg_pool):
    with pg_pool.connection() as conn:
        _claims(conn, A, "admin")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "insert into memberships (user_id, tenant_id, role) values (%s, %s, 'viewer')",
                (U_NEW, B),
            )


def test_admin_update_and_delete_membership(pg_pool):
    with pg_pool.connection() as conn:
        _claims(conn, A, "owner")
        conn.execute(
            "update memberships set role = 'viewer' where user_id = %s and tenant_id = %s",
            (U_NEW, A),
        )
        conn.execute(
            "delete from memberships where user_id = %s and tenant_id = %s", (U_NEW, A)
        )
        conn.commit()


def test_jobs_tenant_isolated_and_app_cannot_update(pg_pool, admin_pool):
    with pg_pool.connection() as conn:
        _claims(conn, A, "planner")
        conn.execute(
            "insert into jobs (tenant_id, kind, payload) values (%s, 'recompute', '{}')",
            (A,),
        )
        conn.commit()
    with pg_pool.connection() as conn:
        _claims(conn, B, "planner")
        assert conn.execute("select count(*) from jobs").fetchone()[0] == 0
    with pg_pool.connection() as conn:
        _claims(conn, A, "planner")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("update jobs set status = 'done'")


def test_idem_index_exists(admin_pool):
    with admin_pool.connection() as conn:
        assert conn.execute(
            "select 1 from pg_indexes where indexname = 'writeback_ledger_idem_idx'"
        ).fetchone()


def test_hook_honors_stored_preference(admin_pool):
    u = "00000000-0000-0000-0000-00000000c2aa"
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into memberships (user_id, tenant_id, role, created_at) values "
            "(%s, %s, 'planner', now() - interval '2 days'), "
            "(%s, %s, 'admin', now() - interval '1 day') on conflict do nothing",
            (u, A, u, B),
        )
        conn.execute(
            "insert into tenant_preferences (user_id, tenant_id) values (%s, %s) "
            "on conflict (user_id) do update set tenant_id = excluded.tenant_id",
            (u, A),
        )
        row = conn.execute(
            "select public.custom_access_token_hook(%s::jsonb)",
            (json.dumps({"user_id": u, "claims": {"sub": u}}),),
        ).fetchone()
        claims = row[0]["claims"]
        assert claims["tenant_id"] == A  # preference beats most-recent (B)


def test_preferences_rls_own_row_only(pg_pool):
    me = "00000000-0000-0000-0000-00000000c2ab"
    other = "00000000-0000-0000-0000-00000000c2ac"
    with pg_pool.connection() as conn:
        as_tenant(conn, A, role="planner", sub=me)
        conn.execute(
            "insert into tenant_preferences (user_id, tenant_id) values (%s::uuid, %s)",
            (me, A),
        )
        conn.commit()
    with pg_pool.connection() as conn:
        as_tenant(conn, A, role="planner", sub=me)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "insert into tenant_preferences (user_id, tenant_id) values (%s::uuid, %s)",
                (other, A),
            )
