"""enqueue_due_recomputes(): eligibility matrix + idempotency.

Runs as pg_admin_conn (superuser) — this function is invoked by pg_cron in
production, never through a request path, so there is no claim to set.
"""
import json


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
