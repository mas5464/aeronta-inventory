from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from psycopg import errors
from trax_io_reco.replay import build_shadow_scorecard

from tests.replay_builders import matched_replay_request, replay_request
from trax_io_spine.pg.replay import (
    PgReplayRunStore,
    mark_replay_run_claimed,
    persist_replay_scorecard,
    replay_fingerprint,
    seed_replay_universe,
)

TENANT_UUID = "99999999-9999-9999-9999-999999990001"
TENANT_SLUG = "replay-t1"
OTHER_TENANT_UUID = "99999999-9999-9999-9999-999999990002"
OTHER_TENANT_SLUG = "replay-t2"


@pytest.fixture(scope="module", autouse=True)
def tenants(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, %s, 'Replay One') "
            "on conflict (id) do nothing",
            (TENANT_UUID, TENANT_SLUG),
        )
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, %s, 'Replay Two') "
            "on conflict (id) do nothing",
            (OTHER_TENANT_UUID, OTHER_TENANT_SLUG),
        )


@pytest.fixture(autouse=True)
def clean_replay_rows(admin_pool, tenants):
    tenant_ids = [TENANT_UUID, OTHER_TENANT_UUID]
    with admin_pool.connection() as conn:
        conn.execute(
            "delete from jobs where tenant_id = any(%s::uuid[]) and kind = 'replay'",
            (tenant_ids,),
        )
        conn.execute(
            "delete from replay_runs where tenant_id = any(%s::uuid[])",
            (tenant_ids,),
        )
    yield
    with admin_pool.connection() as conn:
        conn.execute(
            "delete from jobs where tenant_id = any(%s::uuid[]) and kind = 'replay'",
            (tenant_ids,),
        )
        conn.execute(
            "delete from replay_runs where tenant_id = any(%s::uuid[])",
            (tenant_ids,),
        )


def _store(
    pg_pool,
    *,
    slug: str = TENANT_SLUG,
    uuid: str = TENANT_UUID,
    role: str = "planner",
) -> PgReplayRunStore:
    return PgReplayRunStore(
        pg_pool,
        tenant_slug=slug,
        tenant_uuid=uuid,
        principal=f"{role}-user",
        role=role,
    )


def _seed_and_submit(
    store: PgReplayRunStore,
    admin_pool,
    request,
    *,
    universe_ref: str | None = None,
):
    ref = universe_ref or request.universe_id
    tenant_uuid = (
        TENANT_UUID if request.tenant_id == TENANT_SLUG else OTHER_TENANT_UUID
    )
    seed_replay_universe(
        admin_pool,
        tenant_uuid=tenant_uuid,
        universe_ref=ref,
        request=request,
    )
    return store.submit(
        ref,
        currency=request.currency,
        current_policy_label=request.current_policy_label,
        challenger_policy_label=request.challenger_policy_label,
        comparison_rule=request.comparison_rule,
        match_tolerance=request.match_tolerance,
    )


def test_submission_is_atomic_idempotent_and_advisory(
    pg_pool,
    admin_pool,
) -> None:
    store = _store(pg_pool)
    request = replay_request(TENANT_SLUG)

    first = _seed_and_submit(store, admin_pool, request)
    repeated = _seed_and_submit(store, admin_pool, request)

    assert first.created is True
    assert repeated.created is False
    assert repeated.run == first.run
    assert first.run.status == "queued"
    assert first.run.universe_ref == request.universe_id
    assert first.run.replay_fingerprint == replay_fingerprint(request)
    assert first.run.advisory_only is True
    assert first.run.scorecard is None
    with admin_pool.connection() as conn:
        rows = conn.execute(
            """
            select tenant_id::text, kind, status, payload
            from jobs
            where kind = 'replay' and payload->>'replay_id' = %s
            """,
            (first.run.replay_id,),
        ).fetchall()
    assert rows == [
        (
            TENANT_UUID,
            "replay",
            "queued",
            {"replay_id": first.run.replay_id},
        )
    ]


def test_replay_config_decimal_is_bounded_and_canonical(
    pg_pool,
    admin_pool,
) -> None:
    store = _store(pg_pool)
    request = replay_request(TENANT_SLUG, universe_id="decimal-canonical")
    seed_replay_universe(
        admin_pool,
        tenant_uuid=TENANT_UUID,
        universe_ref=request.universe_id,
        request=request,
    )
    kwargs = {
        "currency": request.currency,
        "current_policy_label": request.current_policy_label,
        "challenger_policy_label": request.challenger_policy_label,
        "comparison_rule": request.comparison_rule,
    }

    first = store.submit(
        request.universe_id,
        match_tolerance=Decimal("0"),
        **kwargs,
    )
    repeated = store.submit(
        request.universe_id,
        match_tolerance=Decimal("0.000000"),
        **kwargs,
    )

    assert first.created is True
    assert repeated.created is False
    assert repeated.run.replay_id == first.run.replay_id
    assert first.run.replay_fingerprint == repeated.run.replay_fingerprint
    for unsafe in (Decimal("1e999999999"), Decimal("1e-999999999")):
        with pytest.raises(ValueError, match="bounded finite decimal"):
            store.submit(
                request.universe_id,
                match_tolerance=unsafe,
                **kwargs,
            )


def test_terminal_scorecard_is_exact_and_immutable(
    pg_pool,
    admin_pool,
    pg_admin_conn,
) -> None:
    store = _store(pg_pool)
    request = replay_request(TENANT_SLUG)
    run = _seed_and_submit(store, admin_pool, request).run
    scorecard = build_shadow_scorecard(request)

    with admin_pool.connection() as conn:
        mark_replay_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            replay_id=run.replay_id,
            attempts=1,
        )
        persist_replay_scorecard(
            conn,
            tenant_uuid=TENANT_UUID,
            replay_id=run.replay_id,
            attempts=1,
            scorecard=scorecard.model_dump(mode="json"),
        )

    completed = store.get(run.replay_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.scorecard is not None
    assert completed.scorecard["excluded_observation_count"] == 1
    assert completed.scorecard["universe_decision_count"] == 1
    assert completed.scorecard["lineage_count"] == 0
    assert len(completed.scorecard["universe_decisions_sha256"]) == 64
    assert len(completed.scorecard["exclusions_sha256"]) == 64
    assert len(completed.scorecard["observation_lineage_sha256"]) == 64
    assert "universe_decisions" not in completed.scorecard
    assert "exclusions" not in completed.scorecard
    assert "observation_lineage" not in completed.scorecard
    exclusions, total = store.exclusion_page(completed.replay_id)
    assert total == 1
    assert exclusions[0].reason_code == "incomplete_horizon"
    assert completed.coverage_rate == 0
    assert completed.detail["writeback_capability"] == "none"
    assert completed.finished_at is not None
    with pytest.raises(errors.RaiseException, match="terminal replay"):
        pg_admin_conn.execute(
            """
            update replay_runs set status = status
            where tenant_id = %s::uuid and replay_id = %s::uuid
            """,
            (TENANT_UUID, run.replay_id),
        )


def test_matched_observation_terminal_path_reconciles_lineage_and_cohort(
    pg_pool,
    admin_pool,
) -> None:
    store = _store(pg_pool)
    request = matched_replay_request(
        TENANT_SLUG,
        universe_id="matched-terminal-evidence",
        include_planning_links=True,
    )
    run = _seed_and_submit(store, admin_pool, request).run
    scorecard = build_shadow_scorecard(request)
    with admin_pool.connection() as conn:
        mark_replay_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            replay_id=run.replay_id,
            attempts=1,
        )
        persist_replay_scorecard(
            conn,
            tenant_uuid=TENANT_UUID,
            replay_id=run.replay_id,
            attempts=1,
            scorecard=scorecard.model_dump(mode="json"),
        )

    completed = store.get(run.replay_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.coverage_rate == 1
    assert completed.scorecard is not None
    assert completed.scorecard["observation_count"] == 1
    assert completed.scorecard["excluded_observation_count"] == 0
    assert completed.scorecard["total_observation_count"] == 1
    assert completed.scorecard["universe_decision_count"] == 1
    assert completed.scorecard["lineage_count"] == 1
    assert completed.scorecard["cohort_count"] == 1
    assert completed.detail["review_package"]["lineage_count"] == 1
    assert completed.detail["review_package"]["exclusion_count"] == 0
    assert completed.detail["review_package"]["cohort_count"] == 1
    expected_lineage_digest = hashlib.sha256(
        json.dumps(
            [
                item.model_dump(mode="json")
                for item in scorecard.observation_lineage
            ],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()
    assert (
        completed.scorecard["observation_lineage_sha256"]
        == expected_lineage_digest
    )
    lineage, lineage_total = store.lineage_page(run.replay_id)
    cohorts, cohort_total = store.cohort_page(run.replay_id)
    assert lineage_total == len(lineage) == 1
    assert lineage[0].lineage["reference"]["current_planning_run_id"] == (
        "11111111-1111-1111-1111-111111111111"
    )
    assert lineage[0].lineage["reference"][
        "current_planning_selection_decision_key"
    ] == request.observations[0].decision_key
    assert cohort_total == len(cohorts) == 1
    assert cohorts[0].observation_count == 1
    assert store.exclusion_page(run.replay_id) == ((), 0)


def test_low_recurring_coverage_matches_decimal_contract(
    pg_pool,
    admin_pool,
) -> None:
    store = _store(pg_pool)
    request = matched_replay_request(
        TENANT_SLUG,
        universe_id="low-recurring-coverage",
        exclusion_count=2_999,
    )
    run = _seed_and_submit(store, admin_pool, request).run
    scorecard = build_shadow_scorecard(request)
    with admin_pool.connection() as conn:
        mark_replay_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            replay_id=run.replay_id,
            attempts=1,
        )
        persist_replay_scorecard(
            conn,
            tenant_uuid=TENANT_UUID,
            replay_id=run.replay_id,
            attempts=1,
            scorecard=scorecard.model_dump(mode="json"),
        )

    expected_coverage = Decimal(1) / Decimal(3_000)
    completed = store.get(run.replay_id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.coverage_rate == expected_coverage
    assert completed.scorecard is not None
    assert completed.scorecard["coverage_rate"] == str(expected_coverage)
    assert completed.scorecard["observation_count"] == 1
    assert completed.scorecard["excluded_observation_count"] == 2_999
    assert completed.scorecard["total_observation_count"] == 3_000
    assert completed.scorecard["universe_decision_count"] == 3_000
    assert completed.scorecard["lineage_count"] == 1
    assert completed.detail["review_package"]["lineage_count"] == 1
    assert completed.detail["review_package"]["exclusion_count"] == 2_999


def test_seed_role_cannot_append_evidence_to_terminal_replay(
    pg_pool,
    admin_pool,
    pg_admin_conn,
) -> None:
    store = _store(pg_pool)
    request = replay_request(TENANT_SLUG, universe_id="terminal-evidence-seal")
    run = _seed_and_submit(store, admin_pool, request).run
    scorecard = build_shadow_scorecard(request)
    with admin_pool.connection() as conn:
        mark_replay_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            replay_id=run.replay_id,
            attempts=1,
        )
        persist_replay_scorecard(
            conn,
            tenant_uuid=TENANT_UUID,
            replay_id=run.replay_id,
            attempts=1,
            scorecard=scorecard.model_dump(mode="json"),
        )

    excluded = request.exclusions[0]
    statements = (
        (
            """
            insert into replay_run_lineage (
              tenant_id, replay_id, observation_id, decision_key,
              as_of, horizon_end, cohort_id, lineage
            ) values (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, '{}'::jsonb)
            """,
            (
                TENANT_UUID,
                run.replay_id,
                "late-lineage",
                excluded.decision_key,
                excluded.as_of,
                excluded.horizon_end,
                "late-cohort",
            ),
        ),
        (
            """
            insert into replay_run_exclusions (
              tenant_id, replay_id, observation_id, decision_key,
              as_of, horizon_end, reason_code, exclusion
            ) values (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, '{}'::jsonb)
            """,
            (
                TENANT_UUID,
                run.replay_id,
                "late-exclusion",
                excluded.decision_key,
                excluded.as_of,
                excluded.horizon_end,
                "incomplete_horizon",
            ),
        ),
        (
            """
            insert into replay_run_cohorts (
              tenant_id, replay_id, cohort_id, observation_count, cohort
            ) values (%s::uuid, %s::uuid, %s, 1, '{}'::jsonb)
            """,
            (TENANT_UUID, run.replay_id, "late-cohort"),
        ),
    )
    pg_admin_conn.execute("set role trax_seed")
    try:
        for statement, params in statements:
            with pytest.raises(
                errors.RaiseException,
                match="terminal replay evidence",
            ):
                pg_admin_conn.execute(statement, params)
    finally:
        pg_admin_conn.execute("reset role")


def test_seed_role_cannot_complete_with_self_declared_zero_evidence(
    pg_pool,
    admin_pool,
    pg_admin_conn,
) -> None:
    store = _store(pg_pool)
    request = replay_request(
        TENANT_SLUG,
        universe_id="direct-terminal-cardinality",
    )
    run = _seed_and_submit(store, admin_pool, request).run
    with admin_pool.connection() as conn:
        mark_replay_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            replay_id=run.replay_id,
            attempts=1,
        )

    pg_admin_conn.execute("set role trax_seed")
    try:
        with pytest.raises(
            errors.RaiseException,
            match="evidence counts",
        ):
            pg_admin_conn.execute(
                """
                update replay_runs
                set status = 'completed',
                    scorecard = %s::jsonb,
                    coverage_rate = 0,
                    finished_at = now()
                where tenant_id = %s::uuid and replay_id = %s::uuid
                """,
                (
                    json.dumps(
                        {
                            "lineage_count": 0,
                            "observation_count": 0,
                            "excluded_observation_count": 0,
                            "total_observation_count": 0,
                            "universe_decision_count": 0,
                            "cohort_count": 0,
                            "coverage_rate": "0",
                        }
                    ),
                    TENANT_UUID,
                    run.replay_id,
                ),
            )
    finally:
        pg_admin_conn.execute("reset role")


def test_forged_scorecard_cannot_be_persisted(
    pg_pool,
    admin_pool,
) -> None:
    store = _store(pg_pool)
    request = replay_request(TENANT_SLUG)
    run = _seed_and_submit(store, admin_pool, request).run
    forged = build_shadow_scorecard(request).model_dump(mode="json")
    forged["current_policy_label"] = "forged-policy"

    with admin_pool.connection() as conn:
        mark_replay_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            replay_id=run.replay_id,
            attempts=1,
        )
    with admin_pool.connection() as conn, pytest.raises(
        ValueError,
        match="does not reconcile",
    ):
        persist_replay_scorecard(
            conn,
            tenant_uuid=TENANT_UUID,
            replay_id=run.replay_id,
            attempts=1,
            scorecard=forged,
        )


def test_preexisting_unbound_evidence_blocks_terminal_replay(
    pg_pool,
    admin_pool,
) -> None:
    store = _store(pg_pool)
    request = replay_request(TENANT_SLUG, universe_id="preexisting-evidence")
    run = _seed_and_submit(store, admin_pool, request).run
    source = request.exclusions[0]
    with admin_pool.connection() as conn:
        mark_replay_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            replay_id=run.replay_id,
            attempts=1,
        )
    with admin_pool.connection() as conn:
        conn.execute(
            """
            insert into replay_run_exclusions (
              tenant_id, replay_id, observation_id, decision_key,
              as_of, horizon_end, reason_code, exclusion
            ) values (
              %s::uuid, %s::uuid, %s, %s, %s, %s,
              'incomplete_horizon', '{}'::jsonb
            )
            """,
            (
                TENANT_UUID,
                run.replay_id,
                "unbound-evidence",
                "PN-UNBOUND@MIA",
                source.as_of,
                source.horizon_end,
            ),
        )

    with admin_pool.connection() as conn, pytest.raises(
        ValueError,
        match="evidence counts",
    ):
        persist_replay_scorecard(
            conn,
            tenant_uuid=TENANT_UUID,
            replay_id=run.replay_id,
            attempts=1,
            scorecard=build_shadow_scorecard(request).model_dump(mode="json"),
        )

    persisted = store.get(run.replay_id)
    assert persisted is not None
    assert persisted.status == "running"
    assert persisted.scorecard is None


def test_rls_isolates_reads_and_viewer_cannot_submit(
    pg_pool,
    admin_pool,
) -> None:
    owner = _store(pg_pool)
    other = _store(
        pg_pool,
        slug=OTHER_TENANT_SLUG,
        uuid=OTHER_TENANT_UUID,
    )
    request = replay_request(TENANT_SLUG)
    run = _seed_and_submit(owner, admin_pool, request).run

    assert other.get(run.replay_id) is None
    assert other.list_recent() == ()

    viewer = _store(pg_pool, role="viewer")
    assert viewer.get(run.replay_id) == run
    with pytest.raises(errors.InsufficientPrivilege):
        viewer.submit(
            request.universe_id,
            currency=request.currency,
            current_policy_label=request.current_policy_label,
            challenger_policy_label=request.challenger_policy_label,
            comparison_rule=request.comparison_rule,
            match_tolerance=request.match_tolerance,
        )


def test_trusted_universe_is_app_read_only_and_cross_tenant_is_nondisclosing(
    pg_pool,
    admin_pool,
) -> None:
    owner = _store(pg_pool)
    other = _store(
        pg_pool,
        slug=OTHER_TENANT_SLUG,
        uuid=OTHER_TENANT_UUID,
    )
    request = replay_request(TENANT_SLUG, universe_id="trusted-only")
    seed_replay_universe(
        admin_pool,
        tenant_uuid=TENANT_UUID,
        universe_ref="opaque-trusted-ref",
        request=request,
    )
    kwargs = {
        "currency": request.currency,
        "current_policy_label": request.current_policy_label,
        "challenger_policy_label": request.challenger_policy_label,
        "comparison_rule": request.comparison_rule,
        "match_tolerance": request.match_tolerance,
    }

    with owner._conn() as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(
            "delete from replay_universes "
            "where tenant_id = %s::uuid and universe_ref = %s",
            (TENANT_UUID, "opaque-trusted-ref"),
        )
    with owner._conn() as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(
            """
            select payload
            from replay_universe_rows
            where tenant_id = %s::uuid and universe_ref = %s
            """,
            (TENANT_UUID, "opaque-trusted-ref"),
        )

    with pytest.raises(LookupError) as cross:
        other.submit("opaque-trusted-ref", **kwargs)
    with pytest.raises(LookupError) as unknown:
        owner.submit("unknown-trusted-ref", **kwargs)
    assert str(cross.value) == str(unknown.value)


def test_trusted_universe_seed_validates_tenant_kind_ids_and_windows(
    admin_pool,
) -> None:
    request = replay_request(
        TENANT_SLUG,
        universe_id="trusted-row-validation",
    )
    seed_replay_universe(
        admin_pool,
        tenant_uuid=TENANT_UUID,
        universe_ref="trusted-row-validation-ref",
        request=request,
    )
    with pytest.raises(ValueError, match="tenant"):
        seed_replay_universe(
            admin_pool,
            tenant_uuid=OTHER_TENANT_UUID,
            universe_ref="cross-tenant-row-validation",
            request=request,
        )

    open_ref = "trusted-row-validation-open-ref"
    with admin_pool.connection() as conn:
        conn.execute(
            """
            insert into replay_universes (
              tenant_id, universe_ref, universe_id, universe_sha256,
              trusted_input_sha256, contract_version, currency,
              expected_decision_count, observation_count, exclusion_count
            ) values (
              %s::uuid, %s, %s, %s, %s, 'replay.v1', 'USD', 4, 0, 4
            )
            on conflict (tenant_id, universe_ref) do nothing
            """,
            (
                TENANT_UUID,
                open_ref,
                "trusted-row-validation-open",
                "e" * 64,
                "f" * 64,
            ),
        )

    payload = request.exclusions[0].model_dump(mode="json")
    invalid_rows = (
        (
            "not-a-kind",
            "kind-mismatch",
            "PN-KIND@MIA",
            request.exclusions[0].as_of,
            request.exclusions[0].horizon_end,
            {
                **payload,
                "observation_id": "kind-mismatch",
                "decision_key": "PN-KIND@MIA",
            },
        ),
        (
            "exclusion",
            "scalar-mismatch",
            "PN-ID@MIA",
            request.exclusions[0].as_of,
            request.exclusions[0].horizon_end,
            {
                **payload,
                "observation_id": "payload-id",
                "decision_key": "PN-ID@MIA",
            },
        ),
        (
            "exclusion",
            "time-mismatch",
            "PN-TIME@MIA",
            request.exclusions[0].as_of + timedelta(days=1),
            request.exclusions[0].horizon_end,
            {
                **payload,
                "observation_id": "time-mismatch",
                "decision_key": "PN-TIME@MIA",
            },
        ),
    )
    for ordinal, invalid in enumerate(invalid_rows):
        with admin_pool.connection() as conn, pytest.raises(
            errors.CheckViolation
        ):
            conn.execute(
                """
                insert into replay_universe_rows (
                  tenant_id, universe_ref, ordinal, row_kind,
                  observation_id, decision_key, as_of, horizon_end, payload
                ) values (
                  %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                """,
                (
                    TENANT_UUID,
                    open_ref,
                    ordinal,
                    *invalid[:-1],
                    json.dumps(invalid[-1]),
                ),
            )


def test_seed_role_cannot_append_to_a_sealed_trusted_universe(
    admin_pool,
    pg_admin_conn,
) -> None:
    request = replay_request(
        TENANT_SLUG,
        universe_id="sealed-trusted-universe",
    )
    universe_ref = "sealed-trusted-universe-ref"
    seed_replay_universe(
        admin_pool,
        tenant_uuid=TENANT_UUID,
        universe_ref=universe_ref,
        request=request,
    )
    source = request.exclusions[0]
    payload = source.model_dump(mode="json")
    payload.update(
        {
            "observation_id": "late-universe-row",
            "decision_key": "PN-LATE@MIA",
        }
    )

    pg_admin_conn.execute("set role trax_seed")
    try:
        with pytest.raises(
            errors.RaiseException,
            match="sealed replay universe",
        ):
            pg_admin_conn.execute(
                """
                insert into replay_universe_rows (
                  tenant_id, universe_ref, ordinal, row_kind,
                  observation_id, decision_key, as_of, horizon_end, payload
                ) values (
                  %s::uuid, %s, %s, 'exclusion', %s, %s, %s, %s, %s::jsonb
                )
                """,
                (
                    TENANT_UUID,
                    universe_ref,
                    request.expected_decision_count,
                    payload["observation_id"],
                    payload["decision_key"],
                    source.as_of,
                    source.horizon_end,
                    json.dumps(payload),
                ),
            )
    finally:
        pg_admin_conn.execute("reset role")


def test_large_scorecard_header_is_bounded_and_exclusions_are_paged(
    pg_pool,
    admin_pool,
) -> None:
    store = _store(pg_pool)
    request = replay_request(
        TENANT_SLUG,
        universe_id="large-trusted-universe",
        decision_count=256,
    )
    run = _seed_and_submit(store, admin_pool, request).run
    scorecard = build_shadow_scorecard(request)
    with admin_pool.connection() as conn:
        mark_replay_run_claimed(
            conn,
            tenant_uuid=TENANT_UUID,
            replay_id=run.replay_id,
            attempts=1,
        )
        bounded = persist_replay_scorecard(
            conn,
            tenant_uuid=TENANT_UUID,
            replay_id=run.replay_id,
            attempts=1,
            scorecard=scorecard.model_dump(mode="json"),
        )
        sizes = conn.execute(
            """
            select pg_column_size(request), pg_column_size(scorecard)
            from replay_runs
            where tenant_id = %s::uuid and replay_id = %s::uuid
            """,
            (TENANT_UUID, run.replay_id),
        ).fetchone()

    assert bounded["excluded_observation_count"] == 256
    assert "exclusions" not in bounded
    assert len(bounded["exclusions_sha256"]) == 64
    assert sizes[0] < 8_192
    assert sizes[1] < 32_768
    first, total = store.exclusion_page(run.replay_id, limit=100)
    second, second_total = store.exclusion_page(
        run.replay_id,
        limit=100,
        offset=100,
    )
    assert total == second_total == 256
    assert len(first) == len(second) == 100
    assert first[0].observation_id < second[0].observation_id
    assert store.lineage_page(run.replay_id) == ((), 0)
