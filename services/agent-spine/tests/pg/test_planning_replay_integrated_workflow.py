from __future__ import annotations

import pytest

from tests.pg.planning_builders import planning_request
from tests.replay_builders import matched_replay_request
from trax_io_spine.pg import worker
from trax_io_spine.pg.planning import PgPlanningRunStore
from trax_io_spine.pg.replay import PgReplayRunStore, seed_replay_universe

TENANT_UUID = "c0dec0de-0000-4000-8000-000000000001"
TENANT_SLUG = "planning-replay-workflow"
OTHER_TENANT_UUID = "c0dec0de-0000-4000-8000-000000000002"
OTHER_TENANT_SLUG = "planning-replay-isolated"


@pytest.fixture(autouse=True)
def workflow_tenants(admin_pool):
    """Own a deterministic queue and two isolated tenants for this workflow."""

    with admin_pool.connection() as conn:
        # The PG harness intentionally shares one database across modules.
        # A workflow test must own the next worker claim deterministically.
        conn.execute("delete from jobs")
        conn.execute(
            "delete from tenants where id in (%s::uuid, %s::uuid)",
            (TENANT_UUID, OTHER_TENANT_UUID),
        )
        conn.execute(
            """
            insert into tenants (id, slug, name)
            values
              (%s::uuid, %s, 'Planning Replay Workflow'),
              (%s::uuid, %s, 'Planning Replay Isolation')
            """,
            (
                TENANT_UUID,
                TENANT_SLUG,
                OTHER_TENANT_UUID,
                OTHER_TENANT_SLUG,
            ),
        )
    yield
    with admin_pool.connection() as conn:
        conn.execute(
            "delete from jobs where tenant_id in (%s::uuid, %s::uuid)",
            (TENANT_UUID, OTHER_TENANT_UUID),
        )
        conn.execute(
            "delete from tenants where id in (%s::uuid, %s::uuid)",
            (TENANT_UUID, OTHER_TENANT_UUID),
        )


def _planning_store(
    pg_pool,
    *,
    tenant_slug: str = TENANT_SLUG,
    tenant_uuid: str = TENANT_UUID,
) -> PgPlanningRunStore:
    return PgPlanningRunStore(
        pg_pool,
        tenant_slug=tenant_slug,
        tenant_uuid=tenant_uuid,
        principal="integrated-planner",
        role="planner",
    )


def _replay_store(
    pg_pool,
    *,
    tenant_slug: str = TENANT_SLUG,
    tenant_uuid: str = TENANT_UUID,
) -> PgReplayRunStore:
    return PgReplayRunStore(
        pg_pool,
        tenant_slug=tenant_slug,
        tenant_uuid=tenant_uuid,
        principal="integrated-planner",
        role="planner",
    )


def _operational_state(admin_pool, tenant_uuid: str) -> tuple[int, int, int, int]:
    """Snapshot action/writeback tables that advisory jobs must never mutate."""

    with admin_pool.connection() as conn:
        return conn.execute(
            """
            select
              (
                select count(*) from recommendations
                where tenant_id = %s::uuid
              ),
              (
                select count(*) from decisions
                where tenant_id = %s::uuid
              ),
              (
                select count(*) from writeback_ledger
                where tenant_id = %s::uuid
              ),
              (
                select count(*) from kill_switches
                where tenant_id = %s::uuid
              )
            """,
            (
                tenant_uuid,
                tenant_uuid,
                tenant_uuid,
                tenant_uuid,
            ),
        ).fetchone()


def test_authorized_planning_then_trusted_replay_is_advisory_and_tenant_isolated(
    pg_pool,
    seed_pool,
    admin_pool,
    monkeypatch,
) -> None:
    planning_store = _planning_store(pg_pool)
    replay_store = _replay_store(pg_pool)
    other_planning_store = _planning_store(
        pg_pool,
        tenant_slug=OTHER_TENANT_SLUG,
        tenant_uuid=OTHER_TENANT_UUID,
    )
    other_replay_store = _replay_store(
        pg_pool,
        tenant_slug=OTHER_TENANT_SLUG,
        tenant_uuid=OTHER_TENANT_UUID,
    )
    operational_before = {
        TENANT_UUID: _operational_state(admin_pool, TENANT_UUID),
        OTHER_TENANT_UUID: _operational_state(admin_pool, OTHER_TENANT_UUID),
    }
    assert operational_before == {
        TENANT_UUID: (0, 0, 0, 0),
        OTHER_TENANT_UUID: (0, 0, 0, 0),
    }

    observed_claims: list[tuple[str, str, str]] = []
    production_planning_handler = worker.HANDLERS["planning"]

    def observe_planning_claim(payload: dict):
        with seed_pool.connection() as conn:
            observed_claims.append(
                (
                    "planning",
                    *conn.execute(
                        """
                        select j.status, r.status
                        from jobs j
                        join planning_runs r
                          on r.tenant_id = j.tenant_id
                         and r.run_id::text = j.payload->>'run_id'
                        where j.tenant_id = %s::uuid
                          and r.run_id = %s::uuid
                        """,
                        (TENANT_UUID, payload["run_id"]),
                    ).fetchone(),
                )
            )
        return production_planning_handler(payload)

    monkeypatch.setattr(worker, "_ingest_pool", seed_pool)
    monkeypatch.setitem(
        worker.HANDLERS,
        "planning",
        observe_planning_claim,
    )

    submitted_plan = planning_store.submit(
        planning_request(
            TENANT_SLUG,
            decision_keys=("PN-INTEGRATED-A@MIA", "PN-INTEGRATED-B@MIA"),
            source_snapshot_hash="integrated-planning-snapshot",
            budget="0",
        )
    )
    assert submitted_plan.created is True
    assert submitted_plan.run.status == "queued"
    assert submitted_plan.run.advisory_only is True
    assert worker.run_once(seed_pool) is True

    completed_plan = planning_store.get(submitted_plan.run.run_id)
    assert completed_plan is not None
    assert completed_plan.status == "completed"
    assert completed_plan.attempts == 1
    assert completed_plan.claimed_at is not None
    assert completed_plan.finished_at is not None
    assert completed_plan.progress_completed == completed_plan.progress_total == 2
    assert planning_store.list_recent()[0] == completed_plan

    selections, selection_total = planning_store.selection_page(
        completed_plan.run_id,
        limit=10,
    )
    assert selection_total == len(selections) == 2
    assert {selection.decision_key for selection in selections} == {
        "PN-INTEGRATED-A@MIA",
        "PN-INTEGRATED-B@MIA",
    }
    for selection in selections:
        assert selection.detail["contract_version"] == "planning-run.v1"
        assert selection.detail["decision_key"] == selection.decision_key
        assert selection.detail["current"]["candidate_id"] == (
            selection.current_candidate_id
        )
        assert selection.detail["selected"]["candidate_id"] == (
            selection.selected_candidate_id
        )
        assert selection.detail["selected_reason"]

    assert other_planning_store.get(completed_plan.run_id) is None
    assert other_planning_store.list_recent() == ()
    assert other_planning_store.selection_page(completed_plan.run_id) == ((), 0)

    trusted_request = matched_replay_request(
        TENANT_SLUG,
        universe_id="integrated-approved-history",
        exclusion_count=1,
    )
    universe_ref = "integrated-approved-history-v1"
    seed_replay_universe(
        seed_pool,
        tenant_uuid=TENANT_UUID,
        universe_ref=universe_ref,
        request=trusted_request,
    )
    production_replay_handler = worker.HANDLERS["replay"]

    def observe_replay_claim(payload: dict):
        with seed_pool.connection() as conn:
            observed_claims.append(
                (
                    "replay",
                    *conn.execute(
                        """
                        select j.status, r.status
                        from jobs j
                        join replay_runs r
                          on r.tenant_id = j.tenant_id
                         and r.replay_id::text = j.payload->>'replay_id'
                        where j.tenant_id = %s::uuid
                          and r.replay_id = %s::uuid
                        """,
                        (TENANT_UUID, payload["replay_id"]),
                    ).fetchone(),
                )
            )
        return production_replay_handler(payload)

    monkeypatch.setitem(worker.HANDLERS, "replay", observe_replay_claim)
    submitted_replay = replay_store.submit(
        universe_ref,
        currency=trusted_request.currency,
        current_policy_label=trusted_request.current_policy_label,
        challenger_policy_label=trusted_request.challenger_policy_label,
        comparison_rule=trusted_request.comparison_rule,
        match_tolerance=trusted_request.match_tolerance,
    )
    assert submitted_replay.created is True
    assert submitted_replay.run.status == "queued"
    assert submitted_replay.run.advisory_only is True
    assert worker.run_once(seed_pool) is True

    completed_replay = replay_store.get(submitted_replay.run.replay_id)
    assert completed_replay is not None
    assert completed_replay.status == "completed"
    assert completed_replay.attempts == 1
    assert completed_replay.claimed_at is not None
    assert completed_replay.finished_at is not None
    assert completed_replay.advisory_only is True
    assert completed_replay.detail["writeback_capability"] == "none"
    assert completed_replay.scorecard is not None
    assert completed_replay.scorecard["observation_count"] == 1
    assert completed_replay.scorecard["excluded_observation_count"] == 1
    assert completed_replay.scorecard["total_observation_count"] == 2
    assert completed_replay.scorecard["coverage_rate"] == "0.5"

    lineage, lineage_total = replay_store.lineage_page(completed_replay.replay_id)
    exclusions, exclusion_total = replay_store.exclusion_page(
        completed_replay.replay_id
    )
    cohorts, cohort_total = replay_store.cohort_page(completed_replay.replay_id)
    assert lineage_total == len(lineage) == 1
    assert exclusion_total == len(exclusions) == 1
    assert cohort_total == len(cohorts) == 1
    assert lineage[0].decision_key == "PN-MATCHED@MIA"
    assert exclusions[0].reason_code == "incomplete_horizon"
    assert cohorts[0].observation_count == 1

    assert observed_claims == [
        ("planning", "running", "running"),
        ("replay", "running", "running"),
    ]
    assert other_replay_store.get(completed_replay.replay_id) is None
    assert other_replay_store.list_recent() == ()
    assert other_replay_store.lineage_page(completed_replay.replay_id) == ((), 0)
    assert other_replay_store.exclusion_page(completed_replay.replay_id) == (
        (),
        0,
    )
    assert other_replay_store.cohort_page(completed_replay.replay_id) == ((), 0)
    with admin_pool.connection() as conn:
        terminal_jobs = conn.execute(
            """
            select kind, status, attempts, error
            from jobs
            where tenant_id = %s::uuid and kind in ('planning', 'replay')
            order by id
            """,
            (TENANT_UUID,),
        ).fetchall()
        isolated_job_count = conn.execute(
            """
            select count(*)
            from jobs
            where tenant_id = %s::uuid and kind in ('planning', 'replay')
            """,
            (OTHER_TENANT_UUID,),
        ).fetchone()[0]
    assert terminal_jobs == [
        ("planning", "done", 1, None),
        ("replay", "done", 1, None),
    ]
    assert isolated_job_count == 0

    operational_after = {
        TENANT_UUID: _operational_state(admin_pool, TENANT_UUID),
        OTHER_TENANT_UUID: _operational_state(admin_pool, OTHER_TENANT_UUID),
    }
    assert operational_after == operational_before
