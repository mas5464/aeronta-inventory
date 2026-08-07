"""Env-gated 58,899-key PostgreSQL planning lifecycle launch gate."""

from __future__ import annotations

import gc
import json
import os
import resource
import sys
import time

import pytest
from trax_io_reco.portfolio.benchmark import (
    FULL_NETWORK_KEY_COUNT,
    FullNetworkBenchmarkConfig,
    build_full_network_benchmark_request,
)

from tests.pg.planning_builders import planning_request_input_coverage
from trax_io_spine.pg import worker
from trax_io_spine.pg.planning import PgPlanningRunStore

_ENABLED = os.environ.get("PG_PLANNING_FULL_LIFECYCLE_BENCH") == "1"
_TENANT_UUID = "58589900-0000-0000-0000-000000000001"
_TENANT_SLUG = "planning-pg-full-network"
_MAX_WALL_SECONDS = 15 * 60


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == "darwin" else value * 1024)


@pytest.mark.skipif(
    not _ENABLED,
    reason="PG_PLANNING_FULL_LIFECYCLE_BENCH=1 is not set",
)
def test_full_network_postgres_planning_lifecycle(
    admin_pool,
    pg_pool,
    monkeypatch,
) -> None:
    """Submit, claim, reconstruct, solve, reconcile, and persist all 58,899 keys."""

    with admin_pool.connection() as conn:
        conn.execute("delete from jobs")
        conn.execute(
            """
            insert into tenants (id, slug, name)
            values (%s::uuid, %s, 'Planning PostgreSQL Full Network')
            on conflict (id) do nothing
            """,
            (_TENANT_UUID, _TENANT_SLUG),
        )
        conn.execute(
            "delete from planning_runs where tenant_id = %s::uuid",
            (_TENANT_UUID,),
        )

    started = time.perf_counter()
    config = FullNetworkBenchmarkConfig(
        tenant_ids=(_TENANT_SLUG, "planning-pg-unused-peer"),
        key_count_per_tenant=FULL_NETWORK_KEY_COUNT,
        solver_time_limit_seconds=300,
        batch_window_seconds=_MAX_WALL_SECONDS,
    )
    request, repair_evidence_key_count = build_full_network_benchmark_request(
        config,
        _TENANT_SLUG,
    )
    coverage = planning_request_input_coverage(request)
    candidate_count = coverage["candidate_count"]
    build_seconds = time.perf_counter() - started
    build_peak_rss_bytes = _peak_rss_bytes()
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=_TENANT_SLUG,
        tenant_uuid=_TENANT_UUID,
        principal="planning-scale-gate",
    )
    submission = store.submit(
        request,
        scope_kind="all_eligible",
        input_coverage=coverage,
        source_generation_hash="planning_generation_" + ("5" * 64),
    )
    run_id = submission.run.run_id
    del request
    gc.collect()
    submit_seconds = time.perf_counter() - started - build_seconds
    submit_peak_rss_bytes = _peak_rss_bytes()

    monkeypatch.setattr(worker, "_ingest_pool", admin_pool)
    worker_started = time.perf_counter()
    assert worker.run_once(admin_pool) is True
    worker_seconds = time.perf_counter() - worker_started
    wall_seconds = time.perf_counter() - started

    completed = store.get(run_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result is not None
    assert completed.result["selection_count"] == FULL_NETWORK_KEY_COUNT
    with admin_pool.connection() as conn:
        menu_count, selection_count = conn.execute(
            """
            select
              (
                select count(*) from planning_run_menus
                where tenant_id = %s::uuid and run_id = %s::uuid
              ),
              (
                select count(*) from planning_run_selections
                where tenant_id = %s::uuid and run_id = %s::uuid
              )
            """,
            (
                _TENANT_UUID,
                run_id,
                _TENANT_UUID,
                run_id,
            ),
        ).fetchone()
        request_size, result_size, detail_size, job_result_size = conn.execute(
            """
            select
              pg_column_size(r.request),
              pg_column_size(r.result),
              pg_column_size(r.detail),
              pg_column_size(j.result)
            from planning_runs r
            join jobs j
              on j.tenant_id = r.tenant_id
             and j.kind = 'planning'
             and j.payload->>'run_id' = r.run_id::text
            where r.tenant_id = %s::uuid and r.run_id = %s::uuid
            """,
            (_TENANT_UUID, run_id),
        ).fetchone()

    metrics = {
        "build_peak_rss_bytes": build_peak_rss_bytes,
        "build_seconds": build_seconds,
        "candidate_count": candidate_count,
        "detail_header_bytes": detail_size,
        "job_result_header_bytes": job_result_size,
        "key_count": FULL_NETWORK_KEY_COUNT,
        "menu_count": menu_count,
        "peak_rss_bytes": _peak_rss_bytes(),
        "repair_evidence_key_count": repair_evidence_key_count,
        "request_header_bytes": request_size,
        "result_header_bytes": result_size,
        "selection_count": selection_count,
        "submit_peak_rss_bytes": submit_peak_rss_bytes,
        "submit_seconds": submit_seconds,
        "wall_seconds": wall_seconds,
        "worker_seconds": worker_seconds,
    }
    print(json.dumps(metrics, sort_keys=True))

    assert menu_count == selection_count == FULL_NETWORK_KEY_COUNT
    assert candidate_count > FULL_NETWORK_KEY_COUNT
    assert all(
        size < 32_768
        for size in (
            request_size,
            result_size,
            detail_size,
            job_result_size,
        )
    )
    assert wall_seconds < _MAX_WALL_SECONDS
