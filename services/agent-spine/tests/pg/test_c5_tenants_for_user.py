"""tenants_for_current_user(): returns the CALLER's memberships only.

Uses pg_admin_conn (autocommit superuser). Claims are transaction-local
(set_config(..., true)), so every claim-scoped call is wrapped in an explicit
transaction — see tests/pg/test_c4_stripe_mirror.py for the same pattern.
"""
import uuid


def _rows_for(conn, user_id: str):
    with conn.transaction():
        conn.execute("set role authenticated")
        conn.execute(
            "select set_config('request.jwt.claims', %s, true)",
            (f'{{"sub":"{user_id}"}}',),
        )
        rows = conn.execute(
            "select slug, role from public.tenants_for_current_user() order by slug"
        ).fetchall()
        conn.execute("reset role")
    return rows


def test_returns_only_callers_memberships(pg_admin_conn):
    u1, u2 = str(uuid.uuid4()), str(uuid.uuid4())
    a = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-a','A') returning id").fetchone()[0]
    b = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-b','B') returning id").fetchone()[0]
    pg_admin_conn.execute(
        "insert into memberships (user_id,tenant_id,role) values (%s,%s,'owner'),(%s,%s,'planner')",
        (u1, a, u1, b))
    pg_admin_conn.execute(
        "insert into memberships (user_id,tenant_id,role) values (%s,%s,'owner')", (u2, b))

    assert _rows_for(pg_admin_conn, u1) == [("c5-a", "owner"), ("c5-b", "planner")]
    # u2 sees ONLY its own membership — no leakage of u1's tenant-a row.
    assert _rows_for(pg_admin_conn, u2) == [("c5-b", "owner")]


def test_no_memberships_returns_empty(pg_admin_conn):
    assert _rows_for(pg_admin_conn, str(uuid.uuid4())) == []
