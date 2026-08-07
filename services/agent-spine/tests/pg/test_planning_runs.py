from __future__ import annotations

from decimal import Decimal

import pytest
from psycopg import errors
from trax_io_reco.contracts.planning import MandatoryFloor, PortfolioSolveRequest
from trax_io_reco.contracts.planning_run import PlanningWarning
from trax_io_reco.portfolio.optimizer import PortfolioOptimizer
from trax_io_reco.portfolio.run import build_planning_run_outcome

from tests.pg.planning_builders import (
    planning_request,
    planning_request_input_coverage,
)
from trax_io_spine.pg.planning import (
    PgPlanningRunStore,
    load_planning_run_work,
    mark_planning_run_claimed,
    persist_planning_result,
)

TENANT_UUID = "77777777-7777-7777-7777-777777770001"
TENANT_SLUG = "planning-t1"
OTHER_TENANT_UUID = "77777777-7777-7777-7777-777777770002"
OTHER_TENANT_SLUG = "planning-t2"


@pytest.fixture(scope="module", autouse=True)
def tenant(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, %s, 'Planning One') "
            "on conflict (id) do nothing",
            (TENANT_UUID, TENANT_SLUG),
        )
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, %s, 'Planning Two') "
            "on conflict (id) do nothing",
            (OTHER_TENANT_UUID, OTHER_TENANT_SLUG),
        )
        conn.commit()


@pytest.fixture(autouse=True)
def clean_planning_rows(admin_pool, tenant):
    tenant_ids = (TENANT_UUID, OTHER_TENANT_UUID)
    with admin_pool.connection() as conn:
        conn.execute(
            "delete from jobs where tenant_id = any(%s::uuid[]) and kind = 'planning'",
            (list(tenant_ids),),
        )
        conn.execute(
            "delete from planning_runs where tenant_id = any(%s::uuid[])",
            (list(tenant_ids),),
        )
    yield
    with admin_pool.connection() as conn:
        conn.execute(
            "delete from jobs where tenant_id = any(%s::uuid[]) and kind = 'planning'",
            (list(tenant_ids),),
        )
        conn.execute(
            "delete from planning_runs where tenant_id = any(%s::uuid[])",
            (list(tenant_ids),),
        )


def test_identical_submission_atomically_reuses_one_run_and_job(
    pg_pool,
    admin_pool,
) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    request = planning_request(
        TENANT_SLUG,
        decision_keys=("PN-A@MIA", "PN-B@MIA"),
        budget="1250",
    )

    first = store.submit(request)
    repeated = store.submit(request)

    assert first.created is True
    assert repeated.created is False
    assert repeated.run == first.run
    assert first.run.status == "queued"
    assert first.run.explicit_scope == ("PN-A@MIA", "PN-B@MIA")
    assert first.run.budget == 1250
    assert first.run.advisory_only is True

    with admin_pool.connection() as conn:
        jobs = conn.execute(
            "select tenant_id::text, kind, status, payload "
            "from jobs where kind = 'planning' and payload->>'run_id' = %s",
            (first.run.run_id,),
        ).fetchall()
    assert jobs == [
        (
            TENANT_UUID,
            "planning",
            "queued",
            {"run_id": first.run.run_id},
        )
    ]


def test_source_generation_is_part_of_run_idempotency(
    pg_pool,
    admin_pool,
) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    request = planning_request(TENANT_SLUG)
    generation_a = "planning_generation_" + ("a" * 64)
    generation_b = "planning_generation_" + ("b" * 64)

    first = store.submit(request, source_generation_hash=generation_a)
    repeated = store.submit(request, source_generation_hash=generation_a)
    newer = store.submit(request, source_generation_hash=generation_b)

    assert first.created is True
    assert repeated.created is False
    assert repeated.run.run_id == first.run.run_id
    assert newer.created is True
    assert newer.run.run_id != first.run.run_id
    assert newer.run.planning_fingerprint == first.run.planning_fingerprint
    assert first.run.source_generation_hash == generation_a
    assert newer.run.source_generation_hash == generation_b
    with admin_pool.connection() as conn:
        assert conn.execute(
            """
            select count(*) from jobs
            where tenant_id = %s::uuid and kind = 'planning'
            """,
            (TENANT_UUID,),
        ).fetchone()[0] == 2


def test_all_eligible_automatic_floors_do_not_expand_rerun_header(
    pg_pool,
    admin_pool,
) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    baseline = planning_request(
        TENANT_SLUG,
        decision_keys=tuple(
            f"PN-AUTO-FLOOR-{index:03d}@MIA" for index in range(256)
        ),
    )
    floor = MandatoryFloor(
        floor_id="automatic-service-floor",
        source="trusted-criticality-policy",
        min_service_level="0",
    )
    request = baseline.model_copy(
        update={
            "menus": tuple(
                menu.model_copy(update={"mandatory_floors": (floor,)})
                for menu in baseline.menus
            )
        }
    )

    run = store.submit(
        request,
        scope_kind="all_eligible",
        input_coverage=planning_request_input_coverage(request),
    ).run

    assert run.request["mandatory_floors"] == {}
    assert run.menu_count == 256
    with admin_pool.connection() as conn:
        assert conn.execute(
            """
            select pg_column_size(request)
            from planning_runs
            where tenant_id = %s::uuid and run_id = %s::uuid
            """,
            (TENANT_UUID, run.run_id),
        ).fetchone()[0] < 16_384


def test_all_eligible_submission_normalizes_menus_and_keeps_header_bounded(
    pg_pool,
    admin_pool,
) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    decision_keys = tuple(f"PN-SCALE-{index:04d}@MIA" for index in range(256))
    request = planning_request(
        TENANT_SLUG,
        decision_keys=decision_keys,
        source_snapshot_hash="scale-snapshot",
        budget="1000",
    )

    with pytest.raises(ValueError, match="input coverage"):
        store.submit(request, scope_kind="all_eligible")

    run = store.submit(
        request,
        scope_kind="all_eligible",
        input_coverage=planning_request_input_coverage(
            request,
            total_key_count=300,
        ),
    ).run

    assert run.scope_kind == "all_eligible"
    assert run.explicit_scope == ()
    assert 0 < len(run.scope_preview) <= 10
    assert run.scope_preview == decision_keys[: len(run.scope_preview)]
    assert run.key_count == run.menu_count == 256
    assert run.candidate_count == run.feasible_candidate_count == 256
    assert "menus" not in run.request
    assert run.coverage["scope_key_count"] == 300
    assert run.coverage["optimized_key_count"] == 256
    assert run.coverage["candidate_menu_key_count"] == 256
    assert run.coverage["skipped_key_count"] == 44
    assert run.coverage["skipped_reason_counts"] == {
        "missing_candidate_frontier": 44
    }
    assert run.skipped_key_count == 44
    assert run.skipped_keys == (
        {
            "reason_code": "missing_candidate_frontier",
            "count": 44,
        },
    )
    assert run.coverage["candidate_menu_coverage_rate"] == str(
        Decimal(256) / Decimal(300)
    )
    with admin_pool.connection() as conn:
        menu_shape = conn.execute(
            """
            select count(*), sum(m.candidate_count), max(m.ordinal),
                   pg_column_size(r.request), pg_column_size(r.coverage)
            from planning_runs r
            join planning_run_menus m
              on m.tenant_id = r.tenant_id and m.run_id = r.run_id
            where r.tenant_id = %s::uuid and r.run_id = %s::uuid
            group by r.request, r.coverage
            """,
            (TENANT_UUID, run.run_id),
        ).fetchone()
    assert menu_shape[:3] == (256, 256, 255)
    assert menu_shape[3] < 8_192
    assert menu_shape[4] < 8_192


def test_worker_reloads_exact_normalized_menus_without_cross_tenant_access(
    pg_pool,
    admin_pool,
) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    request = planning_request(
        TENANT_SLUG,
        decision_keys=tuple(f"PN-RELOAD-{index:03d}@MIA" for index in range(32)),
        source_snapshot_hash="reload-snapshot",
        budget="75",
    )
    run = store.submit(
        request,
        scope_kind="all_eligible",
        input_coverage=planning_request_input_coverage(request),
    ).run
    with admin_pool.connection() as conn:
        mark_planning_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            run_id=run.run_id,
            attempts=1,
        )
        work = load_planning_run_work(
            conn,
            tenant_uuid=TENANT_UUID,
            run_id=run.run_id,
        )
        with pytest.raises(LookupError):
            load_planning_run_work(
                conn,
                tenant_uuid=OTHER_TENANT_UUID,
                run_id=run.run_id,
            )

    reloaded = PortfolioSolveRequest.model_validate(work.request)
    assert reloaded == request
    assert work.planning_fingerprint == run.planning_fingerprint


def test_incomplete_normalized_menu_scope_cannot_be_claimed(
    pg_pool,
    admin_pool,
) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    run = store.submit(
        planning_request(
            TENANT_SLUG,
            source_snapshot_hash="incomplete-menu-scope",
        )
    ).run
    with admin_pool.connection() as conn:
        conn.execute(
            """
            delete from planning_run_menus
            where tenant_id = %s::uuid and run_id = %s::uuid
            """,
            (TENANT_UUID, run.run_id),
        )
        with pytest.raises(LookupError, match="claimable"):
            mark_planning_run_claimed(
                conn,
                tenant_uuid=TENANT_UUID,
                run_id=run.run_id,
                attempts=1,
            )
        status = conn.execute(
            """
            select status
            from planning_runs
            where tenant_id = %s::uuid and run_id = %s::uuid
            """,
            (TENANT_UUID, run.run_id),
        ).fetchone()[0]
    assert status == "queued"


def test_child_run_persists_authoritative_parent_assumption_diff(
    pg_pool,
    admin_pool,
) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    parent_request = planning_request(
        TENANT_SLUG,
        source_snapshot_hash="parent-snapshot",
        budget="100",
    )
    parent = store.submit(parent_request).run
    parent_outcome = build_planning_run_outcome(
        run_id=parent.run_id,
        request=parent_request,
        result=PortfolioOptimizer().solve(parent_request),
    )
    with admin_pool.connection() as conn:
        mark_planning_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            run_id=parent.run_id,
            attempts=1,
        )
        persist_planning_result(
            conn,
            tenant_uuid=TENANT_UUID,
            run_id=parent.run_id,
            attempts=1,
            result=parent_outcome.result.model_dump(mode="json"),
            detail={
                "selection_details": [
                    item.model_dump(mode="json")
                    for item in parent_outcome.selection_details
                ],
                "assumption_diff": [],
                "warnings": [],
            },
        )

    child = store.submit(
        planning_request(
            TENANT_SLUG,
            source_snapshot_hash="parent-snapshot",
            budget="250",
        ),
        parent_run_id=parent.run_id,
    ).run

    assert child.parent_run_id == parent.run_id
    assert child.parent_planning_fingerprint == parent.planning_fingerprint
    assert child.parent_source_snapshot_hash == parent.source_snapshot_hash
    assert child.assumption_diff == (
        {
            "contract_version": "planning-run.v1",
            "field": "budget",
            "before": '"100"',
            "after": '"250"',
        },
    )


def test_terminal_rerun_config_preserves_only_bounded_planner_assumptions(
    pg_pool,
    admin_pool,
) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    baseline = planning_request(
        TENANT_SLUG,
        decision_keys=("PN-RERUN@MIA",),
        budget="321",
    )
    floor = MandatoryFloor(
        floor_id="planner-service-floor",
        source="planner",
        min_service_level="0.8",
    )
    menu = baseline.menus[0].model_copy(
        update={"mandatory_floors": (floor,)}
    )
    request = baseline.model_copy(update={"menus": (menu,)})
    generation = "planning_generation_" + ("c" * 64)
    run = store.submit(
        request,
        source_generation_hash=generation,
        rerun_mandatory_floors={
            menu.frontier.decision_key: menu.mandatory_floors,
        },
    ).run
    outcome = build_planning_run_outcome(
        run_id=run.run_id,
        request=request,
        result=PortfolioOptimizer().solve(request),
    )
    with admin_pool.connection() as conn:
        mark_planning_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            run_id=run.run_id,
            attempts=1,
        )
        persist_planning_result(
            conn,
            tenant_uuid=TENANT_UUID,
            run_id=run.run_id,
            attempts=1,
            result=outcome.result.model_dump(mode="json"),
            detail={
                "selection_details": [
                    item.model_dump(mode="json")
                    for item in outcome.selection_details
                ],
                "assumption_diff": [],
                "warnings": [],
            },
        )

    config = store.rerun_config(run.run_id)
    assert config is not None
    assert config.scope_kind == "explicit"
    assert config.explicit_scope == ("PN-RERUN@MIA",)
    assert config.budget == 321
    assert config.horizon_days == request.horizon_days
    assert config.currency == request.currency
    assert config.source_generation_hash == generation
    assert config.model_profile == run.model_profile
    assert config.objective_weights == request.objective_weights
    assert config.mandatory_floors == {
        "PN-RERUN@MIA": (floor,),
    }
    assert config.time_limit_seconds == request.time_limit_seconds


def test_nonterminal_parent_run_is_rejected(pg_pool) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    parent = store.submit(
        planning_request(
            TENANT_SLUG,
            source_snapshot_hash="nonterminal-parent",
            budget="10",
        )
    ).run

    with pytest.raises(ValueError, match="terminal"):
        store.submit(
            planning_request(
                TENANT_SLUG,
                source_snapshot_hash="nonterminal-parent",
                budget="20",
            ),
            parent_run_id=parent.run_id,
        )


def test_terminal_result_summary_and_selection_are_persisted_atomically(
    pg_pool,
    admin_pool,
    pg_admin_conn,
) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    request = planning_request(
        TENANT_SLUG,
        decision_keys=("PN-TERMINAL@MIA",),
        source_snapshot_hash="terminal-snapshot",
        budget="0",
    )
    run = store.submit(request).run
    solved = PortfolioOptimizer().solve(request)
    outcome = build_planning_run_outcome(
        run_id=run.run_id,
        request=request,
        result=solved,
        warnings=(
            PlanningWarning(
                code="repair_evidence_limited",
                count=2,
                detail="Two warning instances reconcile to the run summary.",
            ),
        ),
    )
    detail = {
        "contract_version": outcome.contract_version,
        "selection_details": [
            item.model_dump(mode="json") for item in outcome.selection_details
        ],
        "assumption_diff": [
            item.model_dump(mode="json") for item in outcome.assumption_diff
        ],
        "warnings": [
            item.model_dump(mode="json") for item in outcome.warnings
        ],
        "huge_blob": "must-not-persist-" * 150_000,
    }

    with admin_pool.connection() as conn:
        mark_planning_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            run_id=run.run_id,
            attempts=1,
        )
        persist_planning_result(
            conn,
            tenant_uuid=TENANT_UUID,
            run_id=run.run_id,
            attempts=1,
            result=outcome.result.model_dump(mode="json"),
            detail=detail,
        )
        detail_size = conn.execute(
            """
            select pg_column_size(detail)
            from planning_runs
            where tenant_id = %s::uuid and run_id = %s::uuid
            """,
            (TENANT_UUID, run.run_id),
        ).fetchone()[0]

    persisted = store.get(run.run_id)
    assert persisted is not None
    assert persisted.status == "completed"
    assert persisted.progress_completed == persisted.progress_total == 1
    assert persisted.result is not None
    assert persisted.result["selection_count"] == 1
    assert persisted.result["planning_fingerprint"] == outcome.planning_fingerprint
    assert "selections" not in persisted.result
    assert "infeasible_keys" not in persisted.result
    assert persisted.summary == outcome.result.summary.model_dump(mode="json")
    assert persisted.warning_count == 2
    assert "selection_details" not in persisted.detail
    assert "huge_blob" not in persisted.detail
    assert detail_size < 4_096
    assert persisted.detail["selection_count"] == 1
    assert persisted.detail["warning_count"] == 2
    assert persisted.finished_at is not None

    selections = store.selections(run.run_id)
    assert len(selections) == 1
    assert selections[0].decision_key == "PN-TERMINAL@MIA"
    assert selections[0].selection == outcome.result.selections[0].model_dump(
        mode="json"
    )
    assert selections[0].detail == outcome.selection_details[0].model_dump(
        mode="json"
    )
    with pytest.raises(errors.RaiseException, match="terminal"):
        pg_admin_conn.execute(
            "update planning_runs set status = status "
            "where tenant_id = %s::uuid and run_id = %s::uuid",
            (TENANT_UUID, run.run_id),
        )


def test_legacy_summary_still_preserves_structured_warning_counts(
    pg_pool,
    admin_pool,
) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    request = planning_request(
        TENANT_SLUG,
        decision_keys=("PN-LEGACY-WARNING@MIA",),
        source_snapshot_hash="legacy-warning-summary",
        budget="0",
    )
    run = store.submit(request).run
    outcome = build_planning_run_outcome(
        run_id=run.run_id,
        request=request,
        result=PortfolioOptimizer().solve(request),
        warnings=(
            PlanningWarning(
                code="repair_evidence_limited",
                count=3,
                detail="Three warning instances use the legacy summary shape.",
            ),
        ),
    )
    result_payload = outcome.result.model_dump(mode="json")
    result_payload["summary"].pop("warning_count")

    with admin_pool.connection() as conn:
        mark_planning_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            run_id=run.run_id,
            attempts=1,
        )
        persist_planning_result(
            conn,
            tenant_uuid=TENANT_UUID,
            run_id=run.run_id,
            attempts=1,
            result=result_payload,
            detail={
                "contract_version": outcome.contract_version,
                "selection_details": [
                    item.model_dump(mode="json")
                    for item in outcome.selection_details
                ],
                "assumption_diff": [],
                "warnings": [
                    item.model_dump(mode="json")
                    for item in outcome.warnings
                ],
            },
        )

    persisted = store.get(run.run_id)
    assert persisted is not None
    assert persisted.warning_count == 3
    assert persisted.detail["warning_count"] == 3


def test_seed_role_cannot_complete_without_exact_normalized_selections(
    pg_pool,
    admin_pool,
    pg_admin_conn,
) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    request = planning_request(
        TENANT_SLUG,
        source_snapshot_hash="direct-terminal-cardinality",
    )
    run = store.submit(request).run
    with admin_pool.connection() as conn:
        mark_planning_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            run_id=run.run_id,
            attempts=1,
        )

    pg_admin_conn.execute("set role trax_seed")
    try:
        with pytest.raises(
            errors.RaiseException,
            match="selection count",
        ):
            pg_admin_conn.execute(
                """
                update planning_runs
                set status = 'completed', finished_at = now()
                where tenant_id = %s::uuid and run_id = %s::uuid
                """,
                (TENANT_UUID, run.run_id),
            )
    finally:
        pg_admin_conn.execute("reset role")


def test_selection_page_is_bounded_filterable_and_tenant_scoped(
    pg_pool,
    admin_pool,
) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    request = planning_request(
        TENANT_SLUG,
        decision_keys=("PN-PAGE-A@MIA", "PN-PAGE-B@MIA", "PN-PAGE-C@MIA"),
        source_snapshot_hash="selection-page-snapshot",
    )
    run = store.submit(request).run
    outcome = build_planning_run_outcome(
        run_id=run.run_id,
        request=request,
        result=PortfolioOptimizer().solve(request),
    )
    with admin_pool.connection() as conn:
        mark_planning_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            run_id=run.run_id,
            attempts=1,
        )
        persist_planning_result(
            conn,
            tenant_uuid=TENANT_UUID,
            run_id=run.run_id,
            attempts=1,
            result=outcome.result.model_dump(mode="json"),
            detail={
                "selection_details": [
                    item.model_dump(mode="json")
                    for item in outcome.selection_details
                ],
                "assumption_diff": [],
                "warnings": [],
            },
        )

    first_page, total = store.selection_page(run.run_id, limit=2, offset=0)
    second_page, second_total = store.selection_page(
        run.run_id,
        limit=2,
        offset=2,
    )
    filtered, filtered_total = store.selection_page(
        run.run_id,
        decision_key="PN-PAGE-B@MIA",
        selected_is_no_change=True,
    )

    assert total == second_total == 3
    assert tuple(item.decision_key for item in first_page) == (
        "PN-PAGE-A@MIA",
        "PN-PAGE-B@MIA",
    )
    assert tuple(item.decision_key for item in second_page) == ("PN-PAGE-C@MIA",)
    assert filtered_total == 1
    assert tuple(item.decision_key for item in filtered) == ("PN-PAGE-B@MIA",)

    other = PgPlanningRunStore(
        pg_pool,
        tenant_slug=OTHER_TENANT_SLUG,
        tenant_uuid=OTHER_TENANT_UUID,
        principal="other-planner",
    )
    assert other.selection_page(run.run_id) == ((), 0)

    with pytest.raises(ValueError, match="between 1 and 100"):
        store.selection_page(run.run_id, limit=101)
    with pytest.raises(ValueError, match="non-negative"):
        store.selection_page(run.run_id, offset=-1)


def test_run_reads_and_submissions_are_tenant_and_role_scoped(pg_pool) -> None:
    owner = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    other_tenant = PgPlanningRunStore(
        pg_pool,
        tenant_slug=OTHER_TENANT_SLUG,
        tenant_uuid=OTHER_TENANT_UUID,
        principal="other-planner",
    )
    run = owner.submit(
        planning_request(
            TENANT_SLUG,
            source_snapshot_hash="tenant-isolation-snapshot",
        )
    ).run

    assert other_tenant.get(run.run_id) is None
    assert other_tenant.selections(run.run_id) == ()

    viewer = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="viewer-user",
        role="viewer",
    )
    with pytest.raises(errors.InsufficientPrivilege):
        viewer.submit(
            planning_request(
                TENANT_SLUG,
                source_snapshot_hash="viewer-cannot-submit",
            )
        )


def test_planning_header_is_trigger_immutable_and_trigger_functions_are_private(
    pg_pool,
    pg_admin_conn,
) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    run = store.submit(
        planning_request(
            TENANT_SLUG,
            source_snapshot_hash="immutable-header",
            budget="75",
        )
    ).run

    with pytest.raises(errors.RaiseException, match="immutable input"):
        pg_admin_conn.execute(
            "update planning_runs set budget = 76 "
            "where tenant_id = %s::uuid and run_id = %s::uuid",
            (TENANT_UUID, run.run_id),
        )

    assert pg_admin_conn.execute(
        """
        select
          has_function_privilege(
            'trax_app',
            'public.enforce_planning_run_immutability()',
            'EXECUTE'
          ),
          has_function_privilege(
            'authenticated',
            'public.reject_planning_selection_update()',
            'EXECUTE'
          )
        """
    ).fetchone() == (False, False)


def test_unreconciled_terminal_payload_cannot_create_an_actionable_plan(
    pg_pool,
    admin_pool,
) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    request = planning_request(
        TENANT_SLUG,
        source_snapshot_hash="unreconciled-terminal",
    )
    run = store.submit(request).run
    outcome = build_planning_run_outcome(
        run_id=run.run_id,
        request=request,
        result=PortfolioOptimizer().solve(request),
    )
    corrupt_result = outcome.result.model_dump(mode="json")
    corrupt_result["summary"]["selected_acquisition_cash"] = "1"

    with admin_pool.connection() as conn:
        mark_planning_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            run_id=run.run_id,
            attempts=1,
        )
        with pytest.raises(ValueError, match="does not reconcile"):
            persist_planning_result(
                conn,
                tenant_uuid=TENANT_UUID,
                run_id=run.run_id,
                attempts=1,
                result=corrupt_result,
                detail={
                    "selection_details": [
                        item.model_dump(mode="json")
                        for item in outcome.selection_details
                    ],
                    "assumption_diff": [],
                    "warnings": [],
                },
            )

    persisted = store.get(run.run_id)
    assert persisted is not None
    assert persisted.status == "running"
    assert persisted.result is None
    assert persisted.summary is None
    assert store.selections(run.run_id) == ()


def test_internally_consistent_fabricated_selection_is_rejected_by_menu_truth(
    pg_pool,
    admin_pool,
) -> None:
    store = PgPlanningRunStore(
        pg_pool,
        tenant_slug=TENANT_SLUG,
        tenant_uuid=TENANT_UUID,
        principal="planner-user",
    )
    request = planning_request(
        TENANT_SLUG,
        source_snapshot_hash="fabricated-selection",
    )
    run = store.submit(request).run
    outcome = build_planning_run_outcome(
        run_id=run.run_id,
        request=request,
        result=PortfolioOptimizer().solve(request),
    )
    forged_result = outcome.result.model_dump(mode="json")
    forged_detail = [
        item.model_dump(mode="json") for item in outcome.selection_details
    ]
    forged_result["selections"][0]["expected_shortage"] = "999"
    forged_result["summary"]["expected_shortage"] = "999"
    forged_detail[0]["selected"]["expected_shortage"] = "999"

    with admin_pool.connection() as conn:
        mark_planning_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            run_id=run.run_id,
            attempts=1,
        )
        with pytest.raises(ValueError, match="frontier|selection detail"):
            persist_planning_result(
                conn,
                tenant_uuid=TENANT_UUID,
                run_id=run.run_id,
                attempts=1,
                result=forged_result,
                detail={
                    "selection_details": forged_detail,
                    "assumption_diff": [],
                    "warnings": [],
                },
            )

    persisted = store.get(run.run_id)
    assert persisted is not None
    assert persisted.status == "running"
    assert persisted.result is None
    assert store.selections(run.run_id) == ()
