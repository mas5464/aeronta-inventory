"""Worker claim/dispatch semantics on the harness (admin pool = trax_seed-grade)."""
import pytest

from trax_io_spine.pg import worker as w

T = "dddddddd-dddd-dddd-dddd-dddddddd0c25"


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
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
