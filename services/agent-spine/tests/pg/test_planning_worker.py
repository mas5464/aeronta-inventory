from __future__ import annotations

import json
import logging

import pytest
from trax_io_reco.contracts.planning import MandatoryFloor

from tests.pg.planning_builders import (
    planning_request,
    planning_request_input_coverage,
)
from trax_io_spine.pg import worker
from trax_io_spine.pg.planning import PgPlanningRunStore

TENANT_UUID = "88888888-8888-8888-8888-888888880001"
TENANT_SLUG = "planning-worker-t1"


@pytest.fixture(scope="module", autouse=True)
def tenant(admin_pool):
    with admin_pool.connection() as conn:
        # Earlier PG modules intentionally leave queue fixtures behind. This
        # worker module must own the next claim deterministically.
        conn.execute("delete from jobs")
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, %s, 'Planning Worker') "
            "on conflict (id) do nothing",
            (TENANT_UUID, TENANT_SLUG),
        )


@pytest.fixture(autouse=True)
def clean_planning_rows(admin_pool, tenant):
    with admin_pool.connection() as conn:
        conn.execute(
            "delete from jobs where tenant_id = %s::uuid and kind = 'planning'",
            (TENANT_UUID,),
        )
        conn.execute(
            "delete from planning_runs where tenant_id = %s::uuid",
            (TENANT_UUID,),
        )
    yield
    with admin_pool.connection() as conn:
        conn.execute(
            "delete from jobs where tenant_id = %s::uuid and kind = 'planning'",
            (TENANT_UUID,),
        )
        conn.execute(
            "delete from planning_runs where tenant_id = %s::uuid",
            (TENANT_UUID,),
        )


def _store(pg_pool) -> PgPlanningRunStore:
    return PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planning-worker-user",
    )


def _job(admin_pool, run_id: str):
    with admin_pool.connection() as conn:
        return conn.execute(
            """
            select status, attempts, error, result
            from jobs
            where tenant_id = %s::uuid and kind = 'planning'
              and payload->>'run_id' = %s
            """,
            (TENANT_UUID, run_id),
        ).fetchone()


def test_worker_claims_solves_and_persists_one_explicit_scope_atomically(
    pg_pool,
    admin_pool,
    monkeypatch,
    caplog,
) -> None:
    store = _store(pg_pool)
    request = planning_request(
        TENANT_SLUG,
        decision_keys=("PN-WORKER-A@MIA", "PN-WORKER-B@MIA"),
        source_snapshot_hash="worker-snapshot",
        budget="0",
    )
    run = store.submit(request).run
    observed_running: list[tuple[str, str]] = []
    production_handler = worker.HANDLERS["planning"]

    def observe_then_solve(payload: dict):
        with admin_pool.connection() as conn:
            observed_running.append(
                conn.execute(
                    """
                    select j.status, r.status
                    from jobs j
                    join planning_runs r
                      on r.tenant_id = j.tenant_id
                     and r.run_id::text = j.payload->>'run_id'
                    where j.tenant_id = %s::uuid and r.run_id = %s::uuid
                    """,
                    (TENANT_UUID, run.run_id),
                ).fetchone()
            )
        return production_handler(payload)

    monkeypatch.setattr(worker, "_ingest_pool", admin_pool)
    monkeypatch.setitem(worker.HANDLERS, "planning", observe_then_solve)

    with caplog.at_level(logging.INFO, logger="trax_io_spine.pg.worker"):
        assert worker.run_once(admin_pool) is True

    assert observed_running == [("running", "running")]
    persisted = store.get(run.run_id)
    assert persisted is not None
    assert persisted.status == "completed"
    assert "menus" not in persisted.request
    assert persisted.request["source_snapshot_hash"] == request.source_snapshot_hash
    assert persisted.menu_count == len(request.menus)
    assert persisted.progress_completed == persisted.progress_total == 2
    assert persisted.finished_at is not None
    assert tuple(item.decision_key for item in store.selections(run.run_id)) == (
        "PN-WORKER-A@MIA",
        "PN-WORKER-B@MIA",
    )
    job_status, attempts, error, job_result = _job(admin_pool, run.run_id)
    assert (job_status, attempts, error) == ("done", 1, None)
    assert job_result == persisted.result
    terminal_events = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "planning_worker_terminal"
    ]
    assert len(terminal_events) == 1
    event = terminal_events[0]
    assert not hasattr(event, "job_id")
    assert event.attempt == 1
    assert event.status == "completed"
    assert event.key_count == 2
    assert event.candidate_count == 2
    assert event.worker_duration_ms >= 0
    assert isinstance(event.solver_duration_ms, float)
    assert event.solver_duration_ms >= 0
    assert event.feasible is True
    assert event.termination in {"optimal", "not_proven"}
    assert event.optimality_gap is None or (
        isinstance(event.optimality_gap, float) and event.optimality_gap >= 0
    )
    assert event.reconciliation == "passed"
    emitted = json.loads(event.getMessage())
    assert emitted["event"] == "planning_worker_terminal"
    assert emitted["candidate_count"] == 2
    assert emitted["reconciliation"] == "passed"
    assert "job_id" not in emitted
    assert "PN-WORKER-A" not in caplog.text
    assert "worker-snapshot" not in caplog.text


def test_worker_normalizes_large_terminal_output_and_keeps_poll_rows_bounded(
    pg_pool,
    admin_pool,
    monkeypatch,
) -> None:
    store = _store(pg_pool)
    request = planning_request(
        TENANT_SLUG,
        decision_keys=tuple(f"PN-OUTPUT-{index:03d}@MIA" for index in range(128)),
        source_snapshot_hash="normalized-output-snapshot",
        budget="0",
    )
    run = store.submit(
        request,
        scope_kind="all_eligible",
        input_coverage=planning_request_input_coverage(
            request,
            total_key_count=140,
        ),
    ).run
    monkeypatch.setattr(worker, "_ingest_pool", admin_pool)

    assert worker.run_once(admin_pool) is True

    persisted = store.get(run.run_id)
    assert persisted is not None
    assert persisted.status == "completed"
    assert persisted.result is not None
    assert persisted.result["selection_count"] == 128
    assert "selections" not in persisted.result
    assert "infeasible_keys" not in persisted.result
    assert "selection_details" not in persisted.detail
    assert persisted.skipped_key_count == 12
    assert persisted.skipped_keys == (
        {
            "reason_code": "missing_candidate_frontier",
            "count": 12,
        },
    )
    assert persisted.detail["input_skipped_key_count"] == 12
    assert persisted.detail["worker_skipped_key_count"] == 0
    assert len(store.selections(run.run_id)) == 128
    job_status, attempts, error, job_result = _job(admin_pool, run.run_id)
    assert (job_status, attempts, error) == ("done", 1, None)
    assert job_result == persisted.result
    with admin_pool.connection() as conn:
        header_sizes = conn.execute(
            """
            select pg_column_size(result), pg_column_size(detail),
                   pg_column_size(summary), pg_column_size(solver)
            from planning_runs
            where tenant_id = %s::uuid and run_id = %s::uuid
            """,
            (TENANT_UUID, run.run_id),
        ).fetchone()
        job_result_size = conn.execute(
            """
            select pg_column_size(result)
            from jobs
            where tenant_id = %s::uuid and kind = 'planning'
              and payload->>'run_id' = %s
            """,
            (TENANT_UUID, run.run_id),
        ).fetchone()[0]
    assert all(size < 16_384 for size in header_sizes)
    assert job_result_size < 16_384


def test_worker_failure_state_and_telemetry_redact_infrastructure_details(
    pg_pool,
    admin_pool,
    monkeypatch,
    caplog,
) -> None:
    store = _store(pg_pool)
    run = store.submit(
        planning_request(
            TENANT_SLUG,
            source_snapshot_hash="sensitive-snapshot-never-log",
        )
    ).run

    def fail(_payload: dict):
        raise RuntimeError("password=never-expose-this")

    monkeypatch.setitem(worker.HANDLERS, "planning", fail)

    with caplog.at_level(logging.WARNING, logger="trax_io_spine.pg.worker"):
        assert worker.run_once(admin_pool) is True

    persisted = store.get(run.run_id)
    assert persisted is not None
    assert persisted.status == "queued"
    assert persisted.result is None
    assert persisted.detail == {
        "error_code": "planning_worker_attempt_failed",
        "failed_attempt": 1,
        "retryable": True,
    }
    job_status, attempts, error, result = _job(admin_pool, run.run_id)
    assert (job_status, attempts, result) == ("queued", 1, None)
    assert json.loads(error) == {
        "error_code": "planning_worker_attempt_failed",
        "retryable": True,
    }
    failure_events = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "planning_worker_failure"
    ]
    assert len(failure_events) == 1
    event = failure_events[0]
    assert event.failure_stage == "handler"
    assert event.error_type == "RuntimeError"
    assert event.terminal is False
    assert event.reconciliation == "not_reached"
    assert event.worker_duration_ms >= 0
    assert "password" not in caplog.text
    assert "sensitive-snapshot-never-log" not in caplog.text


def test_worker_reconciliation_failure_is_safe_and_observable(
    pg_pool,
    admin_pool,
    monkeypatch,
    caplog,
) -> None:
    store = _store(pg_pool)
    run = store.submit(
        planning_request(
            TENANT_SLUG,
            source_snapshot_hash="reconciliation-snapshot-never-log",
        )
    ).run
    production_lifecycle = worker.LIFECYCLES["planning"]

    def fail_persistence(_conn, _job, _output):
        raise RuntimeError("selected_candidate_id=never-expose-this")

    monkeypatch.setattr(worker, "_ingest_pool", admin_pool)
    monkeypatch.setitem(
        worker.LIFECYCLES,
        "planning",
        worker.JobLifecycle(
            on_claim=production_lifecycle.on_claim,
            on_attempt_failed=production_lifecycle.on_attempt_failed,
            on_terminal=fail_persistence,
        ),
    )

    with caplog.at_level(logging.WARNING, logger="trax_io_spine.pg.worker"):
        assert worker.run_once(admin_pool) is True

    persisted = store.get(run.run_id)
    assert persisted is not None
    assert persisted.status == "queued"
    assert persisted.result is None
    assert persisted.detail == {
        "error_code": "planning_worker_attempt_failed",
        "failed_attempt": 1,
        "retryable": True,
    }
    job_status, attempts, error, result = _job(admin_pool, run.run_id)
    assert (job_status, attempts, result) == ("queued", 1, None)
    assert json.loads(error)["error_code"] == "planning_worker_attempt_failed"
    failure_events = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "planning_worker_failure"
    ]
    assert len(failure_events) == 1
    event = failure_events[0]
    assert event.failure_stage == "persistence"
    assert event.error_type == "RuntimeError"
    assert event.terminal is False
    assert event.reconciliation == "failed"
    assert event.candidate_count == 1
    assert event.key_count == 1
    assert event.solver_duration_ms is not None
    assert "selected_candidate_id" not in caplog.text
    assert "reconciliation-snapshot-never-log" not in caplog.text


def test_worker_requeues_then_terminally_fails_run_with_job(
    pg_pool,
    admin_pool,
    monkeypatch,
) -> None:
    store = _store(pg_pool)
    run = store.submit(
        planning_request(
            TENANT_SLUG,
            source_snapshot_hash="retry-snapshot",
        )
    ).run

    def fail(_payload: dict):
        raise RuntimeError("solver infrastructure unavailable")

    monkeypatch.setitem(worker.HANDLERS, "planning", fail)

    for attempt in range(1, worker.MAX_ATTEMPTS + 1):
        assert worker.run_once(admin_pool) is True
        job_status, attempts, error, result = _job(admin_pool, run.run_id)
        persisted = store.get(run.run_id)
        assert persisted is not None
        assert attempts == persisted.attempts == attempt
        expected_code = (
            "planning_worker_attempt_failed"
            if attempt < worker.MAX_ATTEMPTS
            else "planning_worker_failed"
        )
        assert json.loads(error) == {
            "error_code": expected_code,
            "retryable": attempt < worker.MAX_ATTEMPTS,
        }
        assert persisted.detail["error_code"] == expected_code
        assert "solver infrastructure unavailable" not in str(persisted.detail)
        assert result is None
        if attempt < worker.MAX_ATTEMPTS:
            assert job_status == persisted.status == "queued"
            assert persisted.finished_at is None
        else:
            assert job_status == persisted.status == "failed"
            assert persisted.finished_at is not None
            assert store.selections(run.run_id) == ()

    assert worker.run_once(admin_pool) is False


def test_stale_worker_cannot_finalize_a_newer_planning_claim(
    pg_pool,
    admin_pool,
    monkeypatch,
) -> None:
    store = _store(pg_pool)
    run = store.submit(
        planning_request(
            TENANT_SLUG,
            source_snapshot_hash="stale-claim-fence",
        )
    ).run
    production_handler = worker.HANDLERS["planning"]

    def solve_then_reclaim(payload: dict):
        output = production_handler(payload)
        with admin_pool.connection() as conn:
            conn.execute(
                """
                update jobs
                set attempts = attempts + 1, claimed_at = now()
                where tenant_id = %s::uuid and kind = 'planning'
                  and payload->>'run_id' = %s and status = 'running'
                """,
                (TENANT_UUID, run.run_id),
            )
            conn.execute(
                """
                update planning_runs
                set attempts = attempts + 1, claimed_at = now()
                where tenant_id = %s::uuid and run_id = %s::uuid
                  and status = 'running'
                """,
                (TENANT_UUID, run.run_id),
            )
        return output

    monkeypatch.setattr(worker, "_ingest_pool", admin_pool)
    monkeypatch.setitem(worker.HANDLERS, "planning", solve_then_reclaim)

    assert worker.run_once(admin_pool) is True

    persisted = store.get(run.run_id)
    assert persisted is not None
    assert persisted.status == "running"
    assert persisted.attempts == 2
    assert persisted.result is None
    assert store.selections(run.run_id) == ()
    assert _job(admin_pool, run.run_id) == ("running", 2, None, None)


def test_worker_persists_infeasible_guidance_without_actionable_selection(
    pg_pool,
    admin_pool,
    monkeypatch,
    caplog,
) -> None:
    store = _store(pg_pool)
    baseline = planning_request(
        TENANT_SLUG,
        decision_keys=("PN-INFEASIBLE@MIA",),
        source_snapshot_hash="infeasible-snapshot",
    )
    menu = baseline.menus[0].model_copy(
        update={
            "mandatory_floors": (
                MandatoryFloor(
                    floor_id="critical-service-floor",
                    source="tenant-policy",
                    min_service_level="1",
                ),
            )
        }
    )
    request = baseline.model_copy(update={"menus": (menu,)})
    run = store.submit(request).run
    monkeypatch.setattr(worker, "_ingest_pool", admin_pool)

    with caplog.at_level(logging.INFO, logger="trax_io_spine.pg.worker"):
        assert worker.run_once(admin_pool) is True

    persisted = store.get(run.run_id)
    assert persisted is not None
    assert persisted.status == "infeasible"
    assert persisted.summary is None
    assert persisted.result is not None
    assert persisted.result["minimum_budget_required"] == "0"
    assert persisted.result["infeasible_key_count"] == 1
    assert persisted.result["infeasible_key_sample"] == ["PN-INFEASIBLE@MIA"]
    assert "infeasible_keys" not in persisted.result
    assert persisted.progress_completed == persisted.progress_total == 1
    assert store.selections(run.run_id) == ()
    assert _job(admin_pool, run.run_id)[:3] == ("done", 1, None)
    event = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "planning_worker_terminal"
    )
    assert event.status == "infeasible"
    assert event.feasible is False
    assert event.termination == "infeasible"
    assert event.key_count == event.candidate_count == 1
    assert event.reconciliation == "passed"


def test_worker_reaps_a_stale_exhausted_planning_claim(
    pg_pool,
    admin_pool,
) -> None:
    store = _store(pg_pool)
    run = store.submit(
        planning_request(
            TENANT_SLUG,
            source_snapshot_hash="stale-exhausted-snapshot",
        )
    ).run
    with admin_pool.connection() as conn:
        conn.execute(
            """
            update jobs
            set status = 'running',
                attempts = %s,
                claimed_at = now() - (%s || ' seconds')::interval
            where tenant_id = %s::uuid and kind = 'planning'
              and payload->>'run_id' = %s
            """,
            (
                worker.MAX_ATTEMPTS,
                worker.STALE_SECONDS + 1,
                TENANT_UUID,
                run.run_id,
            ),
        )
        conn.execute(
            """
            update planning_runs
            set status = 'running',
                attempts = %s,
                claimed_at = now() - (%s || ' seconds')::interval,
                started_at = now() - (%s || ' seconds')::interval
            where tenant_id = %s::uuid and run_id = %s::uuid
            """,
            (
                worker.MAX_ATTEMPTS,
                worker.STALE_SECONDS + 1,
                worker.STALE_SECONDS + 1,
                TENANT_UUID,
                run.run_id,
            ),
        )

    assert worker.run_once(admin_pool) is True

    persisted = store.get(run.run_id)
    assert persisted is not None
    assert persisted.status == "failed"
    assert persisted.finished_at is not None
    job_status, attempts, error, result = _job(admin_pool, run.run_id)
    assert (job_status, attempts, result) == (
        "failed",
        worker.MAX_ATTEMPTS,
        None,
    )
    assert json.loads(error) == {
        "error_code": "planning_worker_interrupted",
        "retryable": False,
    }
    assert persisted.detail["error_code"] == "planning_worker_interrupted"
    assert "lease expired" not in str(persisted.detail)
