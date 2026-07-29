from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from tests.replay_builders import matched_replay_request
from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.bff.store import PlannerStore
from trax_io_spine.pg import worker
from trax_io_spine.pg.planning import PgPlanningRunStore
from trax_io_spine.pg.replay import PgReplayRunStore
from trax_io_spine.pg.replay_import import import_replay_universe
from trax_io_spine.pg.seed import seed_store
from trax_io_spine.pg.store import PgPlannerStore

EXTRACT = (
    Path(__file__).resolve().parents[3]
    / "recommendation-engine"
    / "examples"
    / "extract_sample"
)
TENANT_UUID = "c0dec0de-1000-4000-8000-000000000001"
TENANT_SLUG = "planning-replay-live-http"
OTHER_TENANT_UUID = "c0dec0de-1000-4000-8000-000000000002"
OTHER_TENANT_SLUG = "planning-replay-live-http-isolated"
AUTH_SECRET = "planning-replay-live-http-secret-0123456789abcdef"
PLANNER_SUB = "c0dec0de-1000-4000-8000-0000000000aa"
DECISION_KEY = "HYD-PUMP-001@YYZ"


@pytest.fixture(autouse=True)
def live_http_tenants(admin_pool):
    """Own the next queue claim and two deterministic isolated tenants."""

    with admin_pool.connection() as conn:
        conn.execute("delete from jobs")
        conn.execute(
            "delete from tenants where id in (%s::uuid, %s::uuid)",
            (TENANT_UUID, OTHER_TENANT_UUID),
        )
        conn.execute(
            """
            insert into tenants (id, slug, name)
            values
              (%s::uuid, %s, 'Planning Replay Live HTTP'),
              (%s::uuid, %s, 'Planning Replay Live HTTP Isolation')
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


def _token(
    tenant_uuid: str,
    *,
    role: str = "planner",
    sub: str = PLANNER_SUB,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": sub,
            "aud": "authenticated",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "tenant_id": tenant_uuid,
            "tenant_role": role,
        },
        AUTH_SECRET,
        algorithm="HS256",
    )


def _headers(
    tenant_uuid: str = TENANT_UUID,
    *,
    role: str = "planner",
) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(tenant_uuid, role=role)}"}


def _planning_body() -> dict:
    return {
        "keys": [{"pn": "HYD-PUMP-001", "location": "YYZ"}],
        "budget": "5000",
        "horizon_days": 60,
        "currency": "USD",
        "objective_weights": {
            "shortage_reduction_weight": "2",
            "aog_risk_reduction_weight": "3",
            "holding_cost_penalty_weight": "0.02",
            "ordering_cost_penalty_weight": "0.01",
            "criticality_weights": {
                "1": "5",
                "2": "3",
                "3": "2",
                "4": "1",
                "5": "1",
            },
        },
        "time_limit_seconds": 15,
    }


def _planning_store_factory(pg_pool, *, tenant_slug: str, tenant_uuid: str):
    def build(*, principal: str, role: str) -> PgPlanningRunStore:
        return PgPlanningRunStore(
            pg_pool,
            tenant_slug=tenant_slug,
            tenant_uuid=tenant_uuid,
            principal=principal,
            role=role,
        )

    return build


def _replay_store_factory(pg_pool, *, tenant_slug: str, tenant_uuid: str):
    def build(*, principal: str, role: str) -> PgReplayRunStore:
        return PgReplayRunStore(
            pg_pool,
            tenant_slug=tenant_slug,
            tenant_uuid=tenant_uuid,
            principal=principal,
            role=role,
        )

    return build


def _operational_state(admin_pool) -> tuple:
    """Return exact tenant-scoped row images for every operational table."""

    tenant_ids = [TENANT_UUID, OTHER_TENANT_UUID]
    with admin_pool.connection() as conn:
        return conn.execute(
            """
            select
              coalesce(
                (
                  select jsonb_agg(to_jsonb(row_value) order by to_jsonb(row_value)::text)
                  from recommendations row_value
                  where tenant_id = any(%s::uuid[])
                ),
                '[]'::jsonb
              ),
              coalesce(
                (
                  select jsonb_agg(to_jsonb(row_value) order by to_jsonb(row_value)::text)
                  from decisions row_value
                  where tenant_id = any(%s::uuid[])
                ),
                '[]'::jsonb
              ),
              coalesce(
                (
                  select jsonb_agg(to_jsonb(row_value) order by to_jsonb(row_value)::text)
                  from writeback_ledger row_value
                  where tenant_id = any(%s::uuid[])
                ),
                '[]'::jsonb
              ),
              coalesce(
                (
                  select jsonb_agg(to_jsonb(row_value) order by to_jsonb(row_value)::text)
                  from kill_switches row_value
                  where tenant_id = any(%s::uuid[])
                ),
                '[]'::jsonb
              )
            """,
            (tenant_ids, tenant_ids, tenant_ids, tenant_ids),
        ).fetchone()


def test_live_http_planning_then_trusted_replay_is_advisory_and_isolated(
    pg_pool,
    seed_pool,
    admin_pool,
    monkeypatch,
    tmp_path,
) -> None:
    source = PlannerStore.from_extract(
        tenant_id=TENANT_SLUG,
        extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    seed_report = seed_store(
        seed_pool,
        store=source,
        slug=TENANT_SLUG,
        name="Planning Replay Live HTTP",
    )
    assert seed_report.tenant_uuid == TENANT_UUID

    planner_stores = {
        TENANT_SLUG: PgPlannerStore(
            pg_pool,
            tenant_slug=TENANT_SLUG,
            tenant_uuid=TENANT_UUID,
        ),
        OTHER_TENANT_SLUG: PgPlannerStore(
            pg_pool,
            tenant_slug=OTHER_TENANT_SLUG,
            tenant_uuid=OTHER_TENANT_UUID,
        ),
    }
    planning_stores = {
        TENANT_SLUG: _planning_store_factory(
            pg_pool,
            tenant_slug=TENANT_SLUG,
            tenant_uuid=TENANT_UUID,
        ),
        OTHER_TENANT_SLUG: _planning_store_factory(
            pg_pool,
            tenant_slug=OTHER_TENANT_SLUG,
            tenant_uuid=OTHER_TENANT_UUID,
        ),
    }
    replay_stores = {
        TENANT_SLUG: _replay_store_factory(
            pg_pool,
            tenant_slug=TENANT_SLUG,
            tenant_uuid=TENANT_UUID,
        ),
        OTHER_TENANT_SLUG: _replay_store_factory(
            pg_pool,
            tenant_slug=OTHER_TENANT_SLUG,
            tenant_uuid=OTHER_TENANT_UUID,
        ),
    }
    app = create_planner_app(
        planner_stores,
        verifier=HsVerifier(AUTH_SECRET),
        tenant_uuids={
            TENANT_SLUG: TENANT_UUID,
            OTHER_TENANT_SLUG: OTHER_TENANT_UUID,
        },
        planning_stores=planning_stores,
        replay_stores=replay_stores,
        planning_enabled_for={TENANT_SLUG, OTHER_TENANT_SLUG},
    )
    client = TestClient(app)
    planner_headers = _headers()
    viewer_headers = _headers(role="viewer")
    other_viewer_headers = _headers(OTHER_TENANT_UUID, role="viewer")
    operational_before = _operational_state(admin_pool)

    submitted_plan = client.post(
        f"/v1/tenants/{TENANT_SLUG}/planning-runs",
        headers=planner_headers,
        json=_planning_body(),
    )
    assert submitted_plan.status_code == 201
    submitted_plan_body = submitted_plan.json()
    assert submitted_plan_body["created"] is True
    assert submitted_plan_body["run"]["status"] == "queued"
    assert submitted_plan_body["run"]["advisory_only"] is True
    plan_run_id = submitted_plan_body["run"]["run_id"]

    monkeypatch.setattr(worker, "_ingest_pool", seed_pool)
    assert worker.run_once(seed_pool) is True

    completed_plan = client.get(
        f"/v1/tenants/{TENANT_SLUG}/planning-runs/{plan_run_id}",
        headers=viewer_headers,
    )
    assert completed_plan.status_code == 200
    completed_plan_body = completed_plan.json()
    assert completed_plan_body["status"] == "completed"
    assert completed_plan_body["advisory_only"] is True
    assert completed_plan_body["attempts"] == 1
    assert completed_plan_body["progress_completed"] == 1
    assert completed_plan_body["progress_total"] == 1

    selection_page = client.get(
        f"/v1/tenants/{TENANT_SLUG}/planning-runs/{plan_run_id}/selections",
        headers=viewer_headers,
        params={
            "limit": 1,
            "offset": 0,
            "decision_key": DECISION_KEY,
        },
    )
    assert selection_page.status_code == 200
    selection_page_body = selection_page.json()
    assert selection_page_body["total"] == 1
    assert selection_page_body["limit"] == 1
    assert selection_page_body["offset"] == 0
    selection = selection_page_body["items"][0]
    assert selection["decision_key"] == DECISION_KEY
    assert selection["detail"]["contract_version"] == "planning-run.v1"
    assert selection["detail"]["current"]["candidate_id"] == (
        selection["current_candidate_id"]
    )
    assert selection["detail"]["selected"]["candidate_id"] == (
        selection["selected_candidate_id"]
    )
    assert selection["detail"]["selected_reason"]

    isolated_plan = client.get(
        f"/v1/tenants/{OTHER_TENANT_SLUG}/planning-runs/{plan_run_id}",
        headers=other_viewer_headers,
    )
    assert isolated_plan.status_code == 404
    assert isolated_plan.json()["detail"]["code"] == "planning_run_not_found"
    cross_tenant_plan = client.get(
        f"/v1/tenants/{TENANT_SLUG}/planning-runs/{plan_run_id}",
        headers=other_viewer_headers,
    )
    assert cross_tenant_plan.status_code == 403

    trusted_request = matched_replay_request(
        TENANT_SLUG,
        universe_id="live-http-approved-history",
        exclusion_count=1,
    )
    trusted_input = tmp_path / "live-http-approved-history.json"
    trusted_input.write_text(
        trusted_request.model_dump_json(),
        encoding="utf-8",
    )
    universe_ref = "live-http-approved-history-v1"
    imported = import_replay_universe(
        seed_pool,
        tenant_uuid=TENANT_UUID,
        universe_ref=universe_ref,
        input_path=trusted_input,
    )
    assert imported.observation_count == 1
    assert imported.exclusion_count == 1

    submitted_replay = client.post(
        f"/v1/tenants/{TENANT_SLUG}/replay-runs",
        headers=planner_headers,
        json={
            "universe_ref": universe_ref,
            "currency": trusted_request.currency,
            "current_policy_label": trusted_request.current_policy_label,
            "challenger_policy_label": trusted_request.challenger_policy_label,
            "comparison_rule": trusted_request.comparison_rule,
            "match_tolerance": str(trusted_request.match_tolerance),
        },
    )
    assert submitted_replay.status_code == 201
    submitted_replay_body = submitted_replay.json()
    assert submitted_replay_body["created"] is True
    assert submitted_replay_body["run"]["status"] == "queued"
    assert submitted_replay_body["run"]["advisory_only"] is True
    replay_id = submitted_replay_body["run"]["replay_id"]

    assert worker.run_once(seed_pool) is True

    completed_replay = client.get(
        f"/v1/tenants/{TENANT_SLUG}/replay-runs/{replay_id}",
        headers=viewer_headers,
    )
    assert completed_replay.status_code == 200
    completed_replay_body = completed_replay.json()
    assert completed_replay_body["status"] == "completed"
    assert completed_replay_body["advisory_only"] is True
    assert completed_replay_body["attempts"] == 1
    assert completed_replay_body["detail"]["writeback_capability"] == "none"
    scorecard = completed_replay_body["scorecard"]
    assert scorecard["advisory_only"] is True
    assert scorecard["observation_count"] == 1
    assert scorecard["excluded_observation_count"] == 1
    assert scorecard["total_observation_count"] == 2
    assert str(scorecard["coverage_rate"]) == "0.5"

    lineage = client.get(
        f"/v1/tenants/{TENANT_SLUG}/replay-runs/{replay_id}/lineage",
        headers=viewer_headers,
        params={"limit": 1, "offset": 0},
    )
    exclusions = client.get(
        f"/v1/tenants/{TENANT_SLUG}/replay-runs/{replay_id}/exclusions",
        headers=viewer_headers,
        params={"limit": 1, "offset": 0, "reason_code": "incomplete_horizon"},
    )
    cohorts = client.get(
        f"/v1/tenants/{TENANT_SLUG}/replay-runs/{replay_id}/cohorts",
        headers=viewer_headers,
        params={"limit": 1, "offset": 0},
    )
    assert lineage.status_code == exclusions.status_code == cohorts.status_code == 200
    assert lineage.json()["total"] == 1
    assert lineage.json()["items"][0]["decision_key"] == "PN-MATCHED@MIA"
    assert exclusions.json()["total"] == 1
    assert exclusions.json()["items"][0]["reason_code"] == "incomplete_horizon"
    assert cohorts.json()["total"] == 1
    assert cohorts.json()["items"][0]["observation_count"] == 1

    isolated_replay = client.get(
        f"/v1/tenants/{OTHER_TENANT_SLUG}/replay-runs/{replay_id}",
        headers=other_viewer_headers,
    )
    assert isolated_replay.status_code == 404
    assert isolated_replay.json()["detail"]["code"] == "replay_run_not_found"

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
    assert _operational_state(admin_pool) == operational_before
