"""Worker claim/dispatch semantics on the harness (admin pool = trax_seed-grade)."""
import pytest

from trax_io_spine.pg import worker as w

T = "dddddddd-dddd-dddd-dddd-dddddddd0c25"


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
        # earlier modules (test_c2_schema) legitimately leave jobs rows behind;
        # this module's claim tests need an empty queue, so clear it once here
        # (module-scoped, local — see Task 5 review) rather than suite-wide.
        conn.execute("delete from jobs")
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, 'acme-c2t5', 'A') "
            "on conflict (id) do nothing",
            (T,),
        )
        conn.commit()


def _enqueue(admin_pool, kind="recompute", payload="{}"):
    with admin_pool.connection() as conn:
        row = conn.execute(
            "insert into jobs (tenant_id, kind, payload) values (%s, %s, %s) returning id",
            (T, kind, payload),
        ).fetchone()
        conn.commit()
        return row[0]


def _status(admin_pool, jid):
    with admin_pool.connection() as conn:
        return conn.execute(
            "select status, attempts, error from jobs where id = %s", (jid,)
        ).fetchone()


def test_unknown_kind_goes_dead(admin_pool):
    jid = _enqueue(admin_pool)
    assert w.run_once(admin_pool) is True
    status, attempts, error = _status(admin_pool, jid)
    assert status == "dead" and "no handler registered" in error


def test_handler_success_marks_done(admin_pool, monkeypatch):
    seen = []
    monkeypatch.setitem(w.HANDLERS, "recompute", lambda payload: seen.append(payload))
    jid = _enqueue(admin_pool, payload='{"x": 1}')
    assert w.run_once(admin_pool) is True
    assert _status(admin_pool, jid)[0] == "done" and seen == [{"x": 1}]


def test_handler_failure_retries_then_fails(admin_pool, monkeypatch):
    def boom(payload):
        raise RuntimeError("kaput")

    monkeypatch.setitem(w.HANDLERS, "recompute", boom)
    jid = _enqueue(admin_pool)
    for expected_attempts in (1, 2, 3):
        assert w.run_once(admin_pool) is True
        status, attempts, error = _status(admin_pool, jid)
        assert attempts == expected_attempts and "kaput" in error
        assert status == ("failed" if expected_attempts == 3 else "queued")
    assert w.run_once(admin_pool) is False  # nothing left to claim


def test_empty_queue_returns_false(admin_pool):
    assert w.run_once(admin_pool) is False


# --- C3 Task 4: claim-durability -------------------------------------------------


def test_claim_commits_before_handler_runs(admin_pool, monkeypatch):
    """The claim (status='running') must already be COMMITTED — visible from a
    separate connection — by the time the handler runs. Under the old single-
    transaction design this would still observe 'queued' (the claim's UPDATE isn't
    committed until the whole block, including the handler call, finishes)."""
    seen_status = []

    def handler(payload):
        status, _attempts, _error = _status(admin_pool, jid)
        seen_status.append(status)

    monkeypatch.setitem(w.HANDLERS, "recompute", handler)
    jid = _enqueue(admin_pool)
    assert w.run_once(admin_pool) is True
    assert seen_status == ["running"]
    assert _status(admin_pool, jid)[0] == "done"  # drained — next test starts clean


def test_handler_crash_leaves_job_reclaimable_not_lost(admin_pool, monkeypatch):
    """A handler that raises AFTER the claim already committed (proving the split-
    transaction design doesn't strand the row) still ends up correctly requeued —
    reclaimable on the next poll, never stuck at 'running' forever."""

    def boom(payload):
        raise RuntimeError("kaput")

    monkeypatch.setitem(w.HANDLERS, "recompute", boom)
    jid = _enqueue(admin_pool)
    assert w.run_once(admin_pool) is True
    status, attempts, error = _status(admin_pool, jid)
    assert status == "queued" and attempts == 1 and "kaput" in error

    # drain it to a terminal state so the module's jobs table ends up empty again.
    monkeypatch.setitem(w.HANDLERS, "recompute", lambda payload: None)
    assert w.run_once(admin_pool) is True
    assert _status(admin_pool, jid)[0] == "done"


def test_stale_running_job_is_reclaimed(admin_pool, monkeypatch):
    """A job stuck at 'running' (its worker crashed before writing the terminal
    update) is reclaimed once `claimed_at` is older than STALE_SECONDS, as long as
    it hasn't exhausted its attempts — a crashed run is never lost forever."""
    jid = _enqueue(admin_pool)
    with admin_pool.connection() as conn:
        conn.execute(
            "update jobs set status = 'running', attempts = 1, "
            "claimed_at = now() - (%s || ' seconds')::interval where id = %s",
            (w.STALE_SECONDS + 5, jid),
        )
        conn.commit()

    seen = []
    monkeypatch.setitem(w.HANDLERS, "recompute", lambda payload: seen.append(payload))
    assert w.run_once(admin_pool) is True
    status, attempts, error = _status(admin_pool, jid)
    assert status == "done" and attempts == 2 and error is None and seen == [{}]


def test_stale_running_job_past_max_attempts_is_not_reclaimed(admin_pool):
    """A stuck 'running' job that already exhausted its attempts is left alone —
    reclaiming it would just retry a job that's effectively already dead."""
    jid = _enqueue(admin_pool)
    with admin_pool.connection() as conn:
        conn.execute(
            "update jobs set status = 'running', attempts = %s, "
            "claimed_at = now() - (%s || ' seconds')::interval where id = %s",
            (w.MAX_ATTEMPTS, w.STALE_SECONDS + 5, jid),
        )
        conn.commit()
    assert w.run_once(admin_pool) is False  # nothing eligible to claim

    # clean up manually — this row was never claimed, so nothing else drains it.
    with admin_pool.connection() as conn:
        conn.execute("delete from jobs where id = %s", (jid,))
        conn.commit()
