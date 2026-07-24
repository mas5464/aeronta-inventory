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

def test_create_tenant_rebinds_existing_preference(pg_admin_conn):
    # C4 final-review fix: a user who already has a tenant_preferences row
    # (from a prior tenant switch / earlier org) must have that preference
    # rebound to the NEW org on create — otherwise the C2 claims hook
    # (requested-claim > stored preference > newest membership) keeps
    # minting JWTs for the OLD tenant after refreshSession, and checkout
    # silently binds the subscription to the wrong tenant.
    uid = str(uuid.uuid4())
    old_tid = pg_admin_conn.execute(
        "insert into tenants (slug, name) values (%s, 'Old Org') returning id",
        (f"old-org-{uid[:8]}",),
    ).fetchone()[0]
    pg_admin_conn.execute(
        "insert into tenant_preferences (user_id, tenant_id) values (%s, %s)",
        (uid, old_tid),
    )
    with pg_admin_conn.transaction():
        pg_admin_conn.execute("set role authenticated")
        pg_admin_conn.execute(
            "select set_config('request.jwt.claims', %s, true)", (f'{{"sub":"{uid}"}}',))
        new_tid = pg_admin_conn.execute(
            "select public.create_tenant_for_current_user('New Org')").fetchone()[0]
        pg_admin_conn.execute("reset role")
    assert new_tid != old_tid
    pref = pg_admin_conn.execute(
        "select tenant_id from tenant_preferences where user_id=%s", (uid,)).fetchone()
    assert pref[0] == new_tid


def test_create_tenant_slug_exhausted_raises_clean_error(pg_admin_conn):
    # We can't monkeypatch gen_random_uuid() from SQL to force a real slug
    # collision deterministically, so we simulate one instead: a BEFORE
    # INSERT trigger on tenants that always raises SQLSTATE 23505
    # (unique_violation) — indistinguishable, from the PL/pgSQL exception
    # handler's point of view, from a genuine unique-constraint hit. This
    # exercises every iteration of the bounded retry loop in
    # create_tenant_for_current_user and proves it surfaces a clean, explicit
    # exception ('could not allocate unique slug') instead of ever letting a
    # raw unique_violation escape to the caller.
    pg_admin_conn.execute(
        "create function _test_force_slug_collision() returns trigger "
        "language plpgsql as $$ "
        "begin raise unique_violation using message = 'forced collision (test)'; "
        "end; $$"
    )
    pg_admin_conn.execute(
        "create trigger _force_collision before insert on tenants "
        "for each row execute function _test_force_slug_collision()"
    )
    try:
        uid = str(uuid.uuid4())
        with (
            pytest.raises(psycopg.errors.RaiseException, match="could not allocate unique slug"),
            pg_admin_conn.transaction(),
        ):
            pg_admin_conn.execute("set role authenticated")
            pg_admin_conn.execute(
                "select set_config('request.jwt.claims', %s, true)",
                (f'{{"sub":"{uid}"}}',))
            pg_admin_conn.execute(
                "select public.create_tenant_for_current_user('Collide Co')")
    finally:
        pg_admin_conn.execute("drop trigger if exists _force_collision on tenants")
        pg_admin_conn.execute("drop function if exists _test_force_slug_collision()")
