from __future__ import annotations

import json

import pytest

from tests.replay_builders import replay_request
from trax_io_spine.pg import worker
from trax_io_spine.pg.replay import PgReplayRunStore, seed_replay_universe

TENANT_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0001"
TENANT_SLUG = "replay-worker-t1"


@pytest.fixture(scope="module", autouse=True)
def tenant(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute("delete from jobs")
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, %s, 'Replay Worker') "
            "on conflict (id) do nothing",
            (TENANT_UUID, TENANT_SLUG),
        )


@pytest.fixture(autouse=True)
def clean_replay_rows(admin_pool, tenant):
    with admin_pool.connection() as conn:
        conn.execute(
            "delete from jobs where tenant_id = %s::uuid and kind = 'replay'",
            (TENANT_UUID,),
        )
        conn.execute(
            "delete from replay_runs where tenant_id = %s::uuid",
            (TENANT_UUID,),
        )
    yield
    with admin_pool.connection() as conn:
        conn.execute(
            "delete from jobs where tenant_id = %s::uuid and kind = 'replay'",
            (TENANT_UUID,),
        )
        conn.execute(
            "delete from replay_runs where tenant_id = %s::uuid",
            (TENANT_UUID,),
        )


def _store(pg_pool) -> PgReplayRunStore:
    return PgReplayRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="replay-worker-user",
    )


def _submit(store, admin_pool, request):
    seed_replay_universe(
        admin_pool,
        tenant_uuid=TENANT_UUID,
        universe_ref=request.universe_id,
        request=request,
    )
    return store.submit(
        request.universe_id,
        currency=request.currency,
        current_policy_label=request.current_policy_label,
        challenger_policy_label=request.challenger_policy_label,
        comparison_rule=request.comparison_rule,
        match_tolerance=request.match_tolerance,
    )


def test_worker_builds_advisory_scorecard_without_writeback(
    pg_pool,
    admin_pool,
    monkeypatch,
) -> None:
    store = _store(pg_pool)
    request = replay_request(TENANT_SLUG)
    run = _submit(store, admin_pool, request).run
    with admin_pool.connection() as conn:
        before = conn.execute(
            "select count(*) from writeback_ledger where tenant_id = %s::uuid",
            (TENANT_UUID,),
        ).fetchone()[0]
    monkeypatch.setattr(worker, "_ingest_pool", admin_pool)

    assert worker.run_once(admin_pool) is True

    completed = store.get(run.replay_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.advisory_only is True
    assert completed.scorecard is not None
    assert completed.scorecard["advisory_only"] is True
    assert completed.detail["writeback_capability"] == "none"
    with admin_pool.connection() as conn:
        job = conn.execute(
            """
            select status, attempts, error, result
            from jobs
            where tenant_id = %s::uuid and kind = 'replay'
              and payload->>'replay_id' = %s
            """,
            (TENANT_UUID, run.replay_id),
        ).fetchone()
        after = conn.execute(
            "select count(*) from writeback_ledger where tenant_id = %s::uuid",
            (TENANT_UUID,),
        ).fetchone()[0]
    assert job[:3] == ("done", 1, None)
    assert job[3] == completed.scorecard
    assert "exclusions" not in job[3]
    exclusions, total = store.exclusion_page(run.replay_id)
    assert total == 1
    assert exclusions[0].reason_code == "incomplete_horizon"
    assert after == before


def test_worker_failure_record_is_safe_and_non_actionable(
    pg_pool,
    admin_pool,
    monkeypatch,
) -> None:
    store = _store(pg_pool)
    run = _submit(
        store,
        admin_pool,
        replay_request(TENANT_SLUG, universe_id="failed-replay"),
    ).run

    def fail(_payload: dict):
        raise RuntimeError("password=never-expose-this")

    monkeypatch.setitem(worker.HANDLERS, "replay", fail)
    for _attempt in range(worker.MAX_ATTEMPTS):
        assert worker.run_once(admin_pool) is True

    failed = store.get(run.replay_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.scorecard is None
    assert failed.detail["error_code"] == "replay_worker_failed"
    assert "password" not in str(failed.detail)
    with admin_pool.connection() as conn:
        job_error = conn.execute(
            """
            select error from jobs
            where tenant_id = %s::uuid and kind = 'replay'
              and payload->>'replay_id' = %s
            """,
            (TENANT_UUID, run.replay_id),
        ).fetchone()[0]
    assert json.loads(job_error) == {
        "error_code": "replay_worker_failed",
        "retryable": False,
    }
    assert "password" not in job_error


def test_worker_reaps_stale_exhausted_replay_claim(
    pg_pool,
    admin_pool,
) -> None:
    store = _store(pg_pool)
    run = _submit(
        store,
        admin_pool,
        replay_request(TENANT_SLUG, universe_id="interrupted-replay"),
    ).run
    with admin_pool.connection() as conn:
        conn.execute(
            """
            update jobs
            set status = 'running',
                attempts = %s,
                claimed_at = now() - (%s || ' seconds')::interval
            where tenant_id = %s::uuid and kind = 'replay'
              and payload->>'replay_id' = %s
            """,
            (
                worker.MAX_ATTEMPTS,
                worker.STALE_SECONDS + 1,
                TENANT_UUID,
                run.replay_id,
            ),
        )
        conn.execute(
            """
            update replay_runs
            set status = 'running',
                attempts = %s,
                claimed_at = now() - (%s || ' seconds')::interval,
                started_at = now() - (%s || ' seconds')::interval
            where tenant_id = %s::uuid and replay_id = %s::uuid
            """,
            (
                worker.MAX_ATTEMPTS,
                worker.STALE_SECONDS + 1,
                worker.STALE_SECONDS + 1,
                TENANT_UUID,
                run.replay_id,
            ),
        )

    assert worker.run_once(admin_pool) is True

    failed = store.get(run.replay_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.detail["error_code"] == "replay_worker_interrupted"
    with admin_pool.connection() as conn:
        status, error = conn.execute(
            """
            select status, error from jobs
            where tenant_id = %s::uuid and kind = 'replay'
              and payload->>'replay_id' = %s
            """,
            (TENANT_UUID, run.replay_id),
        ).fetchone()
    assert status == "failed"
    assert json.loads(error) == {
        "error_code": "replay_worker_interrupted",
        "retryable": False,
    }


def test_stale_worker_cannot_finalize_a_newer_replay_claim(
    pg_pool,
    admin_pool,
    monkeypatch,
) -> None:
    store = _store(pg_pool)
    run = _submit(
        store,
        admin_pool,
        replay_request(TENANT_SLUG, universe_id="stale-replay-fence"),
    ).run
    production_handler = worker.HANDLERS["replay"]

    def evaluate_then_reclaim(payload: dict):
        output = production_handler(payload)
        with admin_pool.connection() as conn:
            conn.execute(
                """
                update jobs
                set attempts = attempts + 1, claimed_at = now()
                where tenant_id = %s::uuid and kind = 'replay'
                  and payload->>'replay_id' = %s and status = 'running'
                """,
                (TENANT_UUID, run.replay_id),
            )
            conn.execute(
                """
                update replay_runs
                set attempts = attempts + 1, claimed_at = now()
                where tenant_id = %s::uuid and replay_id = %s::uuid
                  and status = 'running'
                """,
                (TENANT_UUID, run.replay_id),
            )
        return output

    monkeypatch.setattr(worker, "_ingest_pool", admin_pool)
    monkeypatch.setitem(worker.HANDLERS, "replay", evaluate_then_reclaim)

    assert worker.run_once(admin_pool) is True

    persisted = store.get(run.replay_id)
    assert persisted is not None
    assert persisted.status == "running"
    assert persisted.attempts == 2
    assert persisted.scorecard is None
    with admin_pool.connection() as conn:
        job = conn.execute(
            """
            select status, attempts, error, result
            from jobs
            where tenant_id = %s::uuid and kind = 'replay'
              and payload->>'replay_id' = %s
            """,
            (TENANT_UUID, run.replay_id),
        ).fetchone()
    assert job == ("running", 2, None, None)
