import uuid

import psycopg
import pytest


def test_leads_anon_insert_only(pg_admin_conn):
    pg_admin_conn.execute("set role anon")
    pg_admin_conn.execute(
        "insert into leads (name,email,message,source) "
        "values ('X','x@y.z','hi','pricing')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg_admin_conn.execute("select count(*) from leads")  # no read
    pg_admin_conn.execute("reset role")

def test_create_tenant_for_current_user(pg_admin_conn):
    # pg_admin_conn is autocommit — set_config(..., true) is transaction-local
    # (SET LOCAL semantics), so the role switch + claim + RPC call must share
    # one explicit transaction or the claim never becomes visible to auth.jwt().
    uid = str(uuid.uuid4())
    with pg_admin_conn.transaction():
        pg_admin_conn.execute("set role authenticated")
        pg_admin_conn.execute(
            "select set_config('request.jwt.claims', %s, true)", (f'{{"sub":"{uid}"}}',))
        tid = pg_admin_conn.execute(
            "select public.create_tenant_for_current_user('Acme Air')").fetchone()[0]
        pg_admin_conn.execute("reset role")
    row = pg_admin_conn.execute(
        "select plan_tier from tenants where id=%s", (tid,)).fetchone()
    assert row[0] == "trial"
    mem = pg_admin_conn.execute(
        "select role from memberships where tenant_id=%s and user_id=%s",
        (tid, uid)).fetchone()
    assert mem[0] == "owner"

def test_create_tenant_unique_slug(pg_admin_conn):
    uid = str(uuid.uuid4())
    with pg_admin_conn.transaction():
        pg_admin_conn.execute("set role authenticated")
        pg_admin_conn.execute(
            "select set_config('request.jwt.claims', %s, true)", (f'{{"sub":"{uid}"}}',))
        t1 = pg_admin_conn.execute(
            "select public.create_tenant_for_current_user('Dup Name')").fetchone()[0]
        t2 = pg_admin_conn.execute(
            "select public.create_tenant_for_current_user('Dup Name')").fetchone()[0]
        pg_admin_conn.execute("reset role")
    slugs = pg_admin_conn.execute(
        "select slug from tenants where id in (%s,%s)", (t1, t2)).fetchall()
    assert len({s[0] for s in slugs}) == 2  # slugs differ
