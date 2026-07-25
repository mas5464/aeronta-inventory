"""enqueue_due_recomputes(): eligibility matrix + idempotency.

Runs as pg_admin_conn (superuser) — this function is invoked by pg_cron in
production, never through a request path, so there is no claim to set.

Final review fix (Group D): `revoke ... from public` in the migration does
NOT, on its own, lock this function down on a real Supabase project —
Supabase's platform baseline applies `alter default privileges in schema
public grant all on functions to postgres, anon, authenticated,
service_role`, and those are explicit, NAMED-role grants that a
PUBLIC-scoped revoke has no effect on. Since this function is `security
definer` and PostgREST exposes the `public` schema by default, an unrevoked
grant to any of those three roles would let a request through
`POST /rest/v1/rpc/enqueue_due_recomputes` trigger a cross-tenant recompute
enqueue — a DoS/abuse vector whose return value also leaks how many
active-subscription tenants have replayable data. The
`test_*_role_is_denied_direct_execute` cases below are the regression guard
for the migration's added explicit revoke; `test_trax_seed_can_still_execute`
guards that the fix didn't also lock out the one role that legitimately
needs this (the worker, via pg_cron).
"""
import json

import psycopg
import pytest


def _tenant(conn, slug, status):
    return conn.execute(
        "insert into tenants (slug,name,subscription_status) values (%s,%s,%s) returning id",
        (slug, slug, status)).fetchone()[0]


def _done_ingest(conn, tid, files=None):
    payload = json.dumps({"tenant_id": str(tid), "tenant_slug": "s",
                          "files": files or {"parts": "p/x/parts"}})
    conn.execute(
        "insert into jobs (tenant_id,kind,payload,status) values (%s,'ingest',%s::jsonb,'done')",
        (tid, payload))


def _queued(conn, tid):
    return conn.execute(
        "select payload from jobs where tenant_id=%s and kind='recompute'", (tid,)).fetchall()


def test_eligible_tenant_gets_one_row_with_source_marker_payload(pg_admin_conn):
    t = _tenant(pg_admin_conn, "c5-elig", "active")
    _done_ingest(pg_admin_conn, t, {"parts": "p/b1/parts", "stock": "p/b1/stock"})
    n = pg_admin_conn.execute("select public.enqueue_due_recomputes()").fetchone()[0]
    assert n >= 1
    rows = _queued(pg_admin_conn, t)
    assert len(rows) == 1
    payload = rows[0][0]
    # Regression guard for the reverted-upload bug: the enqueued payload must
    # be EXACTLY the run-time marker, never a snapshot of the prior ingest's
    # payload. The recompute handler resolves the tenant's *latest*
    # status='done' ingest payload itself when the job runs — if this ever
    # goes back to carrying a snapshot captured at enqueue time, a cron tick
    # racing a concurrent upload could enqueue a job that replays stale data
    # over the user's fresh one. Fail loudly if that regresses.
    assert payload == {"source": "recompute"}
    assert "files" not in payload
    assert "tenant_slug" not in payload


def test_no_prior_ingest_is_skipped(pg_admin_conn):
    t = _tenant(pg_admin_conn, "c5-noingest", "active")
    pg_admin_conn.execute("select public.enqueue_due_recomputes()")
    assert _queued(pg_admin_conn, t) == []


def test_lapsed_subscription_is_skipped(pg_admin_conn):
    t = _tenant(pg_admin_conn, "c5-lapsed", "canceled")
    _done_ingest(pg_admin_conn, t)
    pg_admin_conn.execute("select public.enqueue_due_recomputes()")
    assert _queued(pg_admin_conn, t) == []


def test_already_running_is_skipped(pg_admin_conn):
    t = _tenant(pg_admin_conn, "c5-busy", "active")
    _done_ingest(pg_admin_conn, t)
    pg_admin_conn.execute(
        "insert into jobs (tenant_id,kind,payload,status) "
        "values (%s,'ingest','{}'::jsonb,'running')", (t,))
    pg_admin_conn.execute("select public.enqueue_due_recomputes()")
    assert _queued(pg_admin_conn, t) == []


def test_idempotent_second_call_enqueues_nothing_more(pg_admin_conn):
    t = _tenant(pg_admin_conn, "c5-idem", "trialing")
    _done_ingest(pg_admin_conn, t)
    pg_admin_conn.execute("select public.enqueue_due_recomputes()")
    pg_admin_conn.execute("select public.enqueue_due_recomputes()")
    assert len(_queued(pg_admin_conn, t)) == 1  # the queued row blocks a second enqueue


# --- Group D: default-privilege exposure fix -------------------------------


@pytest.mark.parametrize("role", ["anon", "authenticated", "service_role"])
def test_role_is_denied_direct_execute(pg_admin_conn, role):
    """Every role Supabase's own platform baseline would otherwise grant
    EXECUTE to by default must be explicitly denied by this migration — a
    PostgREST-style caller in any of these three roles gets rejected calling
    `enqueue_due_recomputes()` directly, matching the exact role list the
    migration's `revoke ... from anon, authenticated, service_role` targets.
    `tenants_for_current_user()` (migration 0013) is the deliberate
    counter-example: it stays callable by `authenticated` on purpose (see
    test_c5_tenants_for_user.py) because it safely self-filters by caller —
    this function has no such self-filter, so it must reject outright."""
    pg_admin_conn.execute(f"set role {role}")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg_admin_conn.execute("select public.enqueue_due_recomputes()")
    pg_admin_conn.execute("reset role")


def test_trax_seed_can_still_execute(pg_admin_conn):
    """The one role that legitimately needs this (the worker's own DB role,
    granted explicitly in the migration) must be entirely unaffected by the
    Group D revoke — proven end to end with a real eligible tenant, not just
    a bare permission check."""
    t = _tenant(pg_admin_conn, "c5-seed-role", "active")
    _done_ingest(pg_admin_conn, t)

    pg_admin_conn.execute("set role trax_seed")
    n = pg_admin_conn.execute("select public.enqueue_due_recomputes()").fetchone()[0]
    pg_admin_conn.execute("reset role")

    assert n >= 1
    assert len(_queued(pg_admin_conn, t)) == 1
