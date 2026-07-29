from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from tests.replay_builders import replay_request
from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.bff.replay_routes import router
from trax_io_spine.bff.safe_errors import safe_request_validation_handler
from trax_io_spine.pg.replay import (
    PgReplayRunStore,
    ReplayCohortRecord,
    ReplayExclusionRecord,
    ReplayLineageRecord,
    ReplayUniverseRecord,
)


@dataclass
class _Submission:
    run: dict
    created: bool


class _ReplayStore:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.submissions = []
        self.page_calls = []
        self.universe_calls = []

    def submit(
        self,
        universe_ref: str,
        *,
        currency: str,
        current_policy_label: str,
        challenger_policy_label: str,
        comparison_rule: str,
        match_tolerance,
    ):
        self.submissions.append(
            {
                "universe_ref": universe_ref,
                "currency": currency,
                "current_policy_label": current_policy_label,
                "challenger_policy_label": challenger_policy_label,
                "comparison_rule": comparison_rule,
                "match_tolerance": match_tolerance,
            }
        )
        request = replay_request(
            self.tenant_id,
            universe_id=universe_ref,
        )
        return _Submission(
            run={
                "replay_id": "11111111-1111-1111-1111-111111111111",
                "replay_fingerprint": "replay_" + ("a" * 64),
                "input_sha256": "a" * 64,
                "contract_version": "replay.v1",
                "status": "queued",
                "universe_ref": universe_ref,
                "universe_id": request.universe_id,
                "universe_sha256": request.universe_sha256,
                "comparison_rule": request.comparison_rule,
                "expected_decision_count": request.expected_decision_count,
                "advisory_only": True,
                "scorecard": None,
                "coverage_rate": None,
                "detail": {},
                "submitted_by": "planner-user",
                "attempts": 0,
                "claimed_at": None,
                "started_at": None,
                "finished_at": None,
                "created_at": datetime(2026, 7, 28, tzinfo=UTC),
                "updated_at": datetime(2026, 7, 28, tzinfo=UTC),
            },
            created=True,
        )

    def get(self, replay_id: str):
        if replay_id != "11111111-1111-1111-1111-111111111111":
            return None
        return self.submit(
            "historical-decisions",
            currency="USD",
            current_policy_label="current",
            challenger_policy_label="challenger",
            comparison_rule="matched_budget",
            match_tolerance=0,
        ).run

    def list_recent(self, *, limit: int):
        return [
            self.submit(
                "historical-decisions",
                currency="USD",
                current_policy_label="current",
                challenger_policy_label="challenger",
                comparison_rule="matched_budget",
                match_tolerance=0,
            ).run
        ][:limit]

    def list_universes(self, *, limit: int, offset: int):
        self.universe_calls.append((limit, offset))
        universe = ReplayUniverseRecord(
            universe_ref="historical-decisions-2026q1",
            universe_id="historical-decisions",
            universe_sha256="b" * 64,
            trusted_input_sha256="c" * 64,
            contract_version="replay.v1",
            currency="USD",
            expected_decision_count=25,
            observation_count=20,
            exclusion_count=5,
            created_at=datetime(2026, 7, 27, tzinfo=UTC),
        )
        return ((universe,) if offset == 0 else ()), 1

    def lineage_page(
        self,
        replay_id: str,
        *,
        limit: int,
        offset: int,
        observation_id: str | None,
    ):
        self.page_calls.append(
            ("lineage", replay_id, limit, offset, observation_id)
        )
        item = ReplayLineageRecord(
            observation_id="obs-1",
            decision_key="PN-1@MIA",
            as_of=datetime(2026, 1, 1, tzinfo=UTC),
            horizon_end=datetime(2026, 1, 31, tzinfo=UTC),
            cohort_id="criticality:1",
            lineage={"reference": {"observation_id": "obs-1"}},
        )
        return ((item,) if offset == 0 else ()), 1

    def exclusion_page(
        self,
        replay_id: str,
        *,
        limit: int,
        offset: int,
        observation_id: str | None,
        reason_code: str | None,
    ):
        self.page_calls.append(
            (
                "exclusions",
                replay_id,
                limit,
                offset,
                observation_id,
                reason_code,
            )
        )
        item = ReplayExclusionRecord(
            observation_id="obs-2",
            decision_key="PN-2@MIA",
            as_of=datetime(2026, 1, 1, tzinfo=UTC),
            horizon_end=datetime(2026, 1, 31, tzinfo=UTC),
            reason_code="missing_price",
            exclusion={"detail": "No historically effective price."},
        )
        return ((item,) if offset == 0 else ()), 1

    def cohort_page(
        self,
        replay_id: str,
        *,
        limit: int,
        offset: int,
    ):
        self.page_calls.append(("cohorts", replay_id, limit, offset))
        item = ReplayCohortRecord(
            cohort_id="criticality:1",
            observation_count=1,
            cohort={"cohort_id": "criticality:1"},
        )
        return ((item,) if offset == 0 else ()), 1


class _Resolver:
    def __init__(self, tenant_id: str = "tenant-a") -> None:
        self.tenant_id = tenant_id
        self.calls = []

    def __call__(self, body, *, principal: str, role: str):
        self.calls.append((body, principal, role))
        return replay_request(
            self.tenant_id,
            universe_id=body.universe_ref,
        )


def _body(**updates) -> dict:
    body = {
        "universe_ref": "historical-decisions-2026q1",
        "currency": "USD",
        "current_policy_label": "current",
        "challenger_policy_label": "repair-aware",
        "comparison_rule": "matched_budget",
        "match_tolerance": "0",
    }
    body.update(updates)
    return body


def _client(
    *,
    claims: dict | None = None,
    enabled: bool = True,
    resolver_tenant: str = "tenant-a",
) -> tuple[TestClient, _ReplayStore, _Resolver]:
    store = _ReplayStore("tenant-a")
    resolver = _Resolver(resolver_tenant)
    app = FastAPI()
    app.add_exception_handler(
        RequestValidationError,
        safe_request_validation_handler,
    )
    app.state.replay_stores = {"tenant-a": store}
    app.state.replay_universe_resolvers = {"tenant-a": resolver}
    app.state.planning_enabled_for = {"tenant-a": enabled}
    app.state.registry = None

    @app.middleware("http")
    async def claims_middleware(request, call_next):
        if claims is not None:
            request.state.claims = claims
        return await call_next(request)

    app.include_router(router)
    return TestClient(app), store, resolver


def _claims(role: str = "planner") -> dict:
    return {
        "sub": "planner-user",
        "tenant_id": "tenant-a-uuid",
        "tenant_role": role,
    }


def test_submit_returns_immutable_advisory_run() -> None:
    client, store, resolver = _client(claims=_claims())

    response = client.post(
        "/v1/tenants/tenant-a/replay-runs",
        json=_body(),
    )

    assert response.status_code == 201
    assert response.json()["created"] is True
    assert response.json()["run"]["status"] == "queued"
    assert response.json()["run"]["advisory_only"] is True
    assert store.submissions[0]["universe_ref"] == "historical-decisions-2026q1"
    assert resolver.calls[0][0].universe_ref == "historical-decisions-2026q1"
    assert store.tenant_id == "tenant-a"
    assert resolver.calls[0][1:] == ("planner-user", "planner")


def test_pg_submission_resolves_the_trusted_universe_only_once() -> None:
    client, backing_store, resolver = _client(claims=_claims())
    pg_store = object.__new__(PgReplayRunStore)
    pg_store.submit = backing_store.submit  # type: ignore[method-assign]
    client.app.state.replay_stores["tenant-a"] = pg_store

    response = client.post(
        "/v1/tenants/tenant-a/replay-runs",
        json=_body(),
    )

    assert response.status_code == 201
    assert resolver.calls == []
    assert len(backing_store.submissions) == 1


def test_pg_submission_maps_trusted_lookup_failures_to_one_safe_422() -> None:
    client, _backing_store, resolver = _client(claims=_claims())
    pg_store = object.__new__(PgReplayRunStore)

    def missing(*_args, **_kwargs):
        raise LookupError("tenant uuid and hidden universe key")

    pg_store.submit = missing  # type: ignore[method-assign]
    client.app.state.replay_stores["tenant-a"] = pg_store

    response = client.post(
        "/v1/tenants/tenant-a/replay-runs",
        json=_body(),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "replay_universe_invalid"
    assert resolver.calls == []
    assert "tenant uuid" not in response.text
    assert "hidden universe" not in response.text


def test_submit_rejects_client_observations_metrics_and_lineage() -> None:
    client, store, resolver = _client(claims=_claims())

    response = client.post(
        "/v1/tenants/tenant-a/replay-runs",
        json=_body(
            observations=[],
            universe_decisions=[],
            scorecard={"fill_rate": "1"},
            lineage=[],
        ),
    )

    assert response.status_code == 422
    assert store.submissions == []
    assert resolver.calls == []


def test_trusted_resolver_cannot_cross_tenants() -> None:
    client, store, _resolver = _client(
        claims=_claims(),
        resolver_tenant="tenant-b",
    )

    response = client.post(
        "/v1/tenants/tenant-a/replay-runs",
        json=_body(),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "replay_universe_invalid"
    assert store.submissions == []


def test_unknown_and_cross_tenant_universe_refs_are_indistinguishable() -> None:
    cross_client, _store, _resolver = _client(
        claims=_claims(),
        resolver_tenant="tenant-b",
    )
    unknown_client, _unknown_store, _unknown_resolver = _client(
        claims=_claims()
    )

    def missing(*_args, **_kwargs):
        raise LookupError("secret storage key")

    unknown_client.app.state.replay_universe_resolvers["tenant-a"] = missing
    cross = cross_client.post(
        "/v1/tenants/tenant-a/replay-runs",
        json=_body(),
    )
    unknown = unknown_client.post(
        "/v1/tenants/tenant-a/replay-runs",
        json=_body(),
    )

    assert cross.status_code == unknown.status_code == 422
    assert cross.json() == unknown.json()
    assert "tenant-b" not in cross.text
    assert "secret storage key" not in unknown.text


def test_submission_conflict_redacts_store_and_contract_details() -> None:
    client, store, _resolver = _client(claims=_claims())

    def conflicting(*_args, **_kwargs):
        raise ValueError(
            "tenant 99999999-9999-9999-9999-999999999999 "
            "fingerprint replay_secret driver failure"
        )

    store.submit = conflicting  # type: ignore[method-assign]
    response = client.post(
        "/v1/tenants/tenant-a/replay-runs",
        json=_body(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "replay_submission_conflict",
        "message": (
            "The immutable replay conflicts with existing lineage or "
            "tenant-scoped submission state."
        ),
        "retryable": False,
    }
    assert "99999999" not in response.text
    assert "replay_secret" not in response.text
    assert "driver" not in response.text


def test_viewer_can_read_but_cannot_submit() -> None:
    client, store, _resolver = _client(claims=_claims("viewer"))

    read = client.get("/v1/tenants/tenant-a/replay-runs")
    write = client.post(
        "/v1/tenants/tenant-a/replay-runs",
        json=_body(),
    )

    assert read.status_code == 200
    assert write.status_code == 403
    assert store.submissions  # read fixture construction only


def test_universe_metadata_is_bounded_opaque_and_planner_only() -> None:
    client, store, _resolver = _client(claims=_claims("planner"))

    response = client.get(
        "/v1/tenants/tenant-a/replay-runs/universes?limit=25&offset=0"
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["limit"] == 25
    assert response.json()["items"] == [
        {
            "universe_ref": "historical-decisions-2026q1",
            "universe_id": "historical-decisions",
            "universe_sha256": "b" * 64,
            "contract_version": "replay.v1",
            "currency": "USD",
            "expected_decision_count": 25,
            "observation_count": 20,
            "exclusion_count": 5,
            "created_at": "2026-07-27T00:00:00Z",
        }
    ]
    assert "trusted_input_sha256" not in response.text
    assert "observations" not in response.text
    assert store.universe_calls == [(25, 0)]


def test_universe_metadata_accepts_the_shared_256_character_boundary() -> None:
    client, store, _resolver = _client(claims=_claims("planner"))

    def boundary(*, limit: int, offset: int):
        return (
            (
                ReplayUniverseRecord(
                    universe_ref="r" * 256,
                    universe_id="u" * 256,
                    universe_sha256="b" * 64,
                    trusted_input_sha256="c" * 64,
                    contract_version="replay.v1",
                    currency="USD",
                    expected_decision_count=1,
                    observation_count=1,
                    exclusion_count=0,
                    created_at=datetime(2026, 7, 27, tzinfo=UTC),
                ),
            ),
            1,
        )

    store.list_universes = boundary  # type: ignore[method-assign]
    response = client.get(
        "/v1/tenants/tenant-a/replay-runs/universes"
    )

    assert response.status_code == 200
    assert len(response.json()["items"][0]["universe_ref"]) == 256
    assert len(response.json()["items"][0]["universe_id"]) == 256


def test_universe_metadata_enforces_feature_auth_and_page_bounds() -> None:
    anonymous, _store, _resolver = _client()
    disabled, _disabled_store, _disabled_resolver = _client(
        claims=_claims("viewer"),
        enabled=False,
    )
    viewer, store, _viewer_resolver = _client(claims=_claims("viewer"))
    path = "/v1/tenants/tenant-a/replay-runs/universes"

    assert anonymous.get(path).status_code == 401
    assert disabled.get(path).status_code == 404
    assert viewer.get(path).status_code == 403
    planner, planner_store, _planner_resolver = _client(
        claims=_claims("planner")
    )
    assert planner.get(path + "?limit=101").status_code == 422
    assert store.universe_calls == []
    assert planner_store.universe_calls == []


def test_submit_rejects_unbounded_match_tolerance() -> None:
    client, store, resolver = _client(claims=_claims())

    responses = [
        client.post(
            "/v1/tenants/tenant-a/replay-runs",
            json=_body(match_tolerance=value),
        )
        for value in ("1e1000000", "0.1234567890123")
    ]

    assert [response.status_code for response in responses] == [422, 422]
    assert store.submissions == []
    assert resolver.calls == []


def test_validation_failure_uses_redacted_locked_error_envelope() -> None:
    client, store, resolver = _client(claims=_claims())
    rejected_input = "tenant-secret-historical-observation"

    response = client.post(
        "/v1/tenants/tenant-a/replay-runs",
        json=_body(match_tolerance=rejected_input),
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "replay_request_invalid",
            "message": (
                "The replay request does not match the supported contract."
            ),
            "retryable": False,
        }
    }
    assert rejected_input not in response.text
    assert store.submissions == []
    assert resolver.calls == []


def test_unknown_run_is_not_disclosed() -> None:
    client, _store, _resolver = _client(claims=_claims("viewer"))

    response = client.get(
        "/v1/tenants/tenant-a/replay-runs/22222222-2222-2222-2222-222222222222"
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "replay_run_not_found"


def test_bounded_replay_evidence_pages_forward_filters_and_counts() -> None:
    client, store, _resolver = _client(claims=_claims("viewer"))
    base = (
        "/v1/tenants/tenant-a/replay-runs/"
        "11111111-1111-1111-1111-111111111111"
    )

    lineage = client.get(
        base + "/lineage?limit=25&offset=0&observation_id=obs-1"
    )
    exclusions = client.get(
        base
        + "/exclusions?limit=10&offset=0"
        + "&observation_id=obs-2&reason_code=missing_price"
    )
    cohorts = client.get(base + "/cohorts?limit=5&offset=0")

    assert lineage.status_code == exclusions.status_code == cohorts.status_code == 200
    assert lineage.json()["total"] == 1
    assert lineage.json()["items"][0]["observation_id"] == "obs-1"
    assert exclusions.json()["items"][0]["reason_code"] == "missing_price"
    assert cohorts.json()["items"][0]["observation_count"] == 1
    assert store.page_calls == [
        (
            "lineage",
            "11111111-1111-1111-1111-111111111111",
            25,
            0,
            "obs-1",
        ),
        (
            "exclusions",
            "11111111-1111-1111-1111-111111111111",
            10,
            0,
            "obs-2",
            "missing_price",
        ),
        (
            "cohorts",
            "11111111-1111-1111-1111-111111111111",
            5,
            0,
        ),
    ]


def test_replay_evidence_pages_are_bounded_and_do_not_disclose_unknown_runs() -> None:
    client, store, _resolver = _client(claims=_claims("viewer"))
    unknown = client.get(
        "/v1/tenants/tenant-a/replay-runs/"
        "22222222-2222-2222-2222-222222222222/lineage"
    )
    oversized = client.get(
        "/v1/tenants/tenant-a/replay-runs/"
        "11111111-1111-1111-1111-111111111111/exclusions?limit=101"
    )

    assert unknown.status_code == 404
    assert oversized.status_code == 422
    assert store.page_calls == []


def test_routes_require_verified_claims_even_in_direct_router_tests() -> None:
    client, _store, _resolver = _client()

    response = client.get("/v1/tenants/tenant-a/replay-runs")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "replay_auth_required"


def test_replay_resource_has_no_update_delete_or_commit_surface() -> None:
    client, _store, _resolver = _client(claims=_claims())
    path = (
        "/v1/tenants/tenant-a/replay-runs/"
        "11111111-1111-1111-1111-111111111111"
    )

    assert client.patch(path, json={}).status_code == 405
    assert client.put(path, json={}).status_code == 405
    assert client.delete(path).status_code == 405
    assert client.post(path + "/commit", json={}).status_code == 404


def test_replay_uses_the_same_default_off_tenant_capability_gate() -> None:
    client, store, resolver = _client(claims=_claims(), enabled=False)

    capability = client.get(
        "/v1/tenants/tenant-a/replay-runs/capabilities"
    )
    read = client.get("/v1/tenants/tenant-a/replay-runs")
    write = client.post(
        "/v1/tenants/tenant-a/replay-runs",
        json=_body(),
    )

    assert capability.status_code == 200
    assert capability.json()["enabled"] is False
    assert capability.json()["can_read"] is False
    assert capability.json()["advisory_only"] is True
    assert read.status_code == 404
    assert read.json()["detail"]["code"] == "replay_feature_disabled"
    assert write.status_code == 404
    assert store.submissions == []
    assert resolver.calls == []


def _token(tenant_uuid: str, role: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "verified-user",
            "aud": "authenticated",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "tenant_id": tenant_uuid,
            "tenant_role": role,
        },
        "replay-test-secret-0123456789abcdef",
        algorithm="HS256",
    )


def test_full_app_auth_enforces_tenant_claim_and_planner_write_floor() -> None:
    tenant_uuid = "33333333-3333-3333-3333-333333333333"
    store = _ReplayStore("tenant-a")
    app = create_planner_app(
        {},
        verifier=HsVerifier("replay-test-secret-0123456789abcdef"),
        tenant_uuids={"tenant-a": tenant_uuid},
        replay_stores={"tenant-a": store},
        replay_universe_resolvers={"tenant-a": _Resolver()},
        planning_enabled_for={"tenant-a": True},
    )
    client = TestClient(app)
    path = "/v1/tenants/tenant-a/replay-runs"

    viewer_read = client.get(
        path,
        headers={"Authorization": f"Bearer {_token(tenant_uuid, 'viewer')}"},
    )
    viewer_write = client.post(
        path,
        headers={"Authorization": f"Bearer {_token(tenant_uuid, 'viewer')}"},
        json=_body(),
    )
    cross_tenant = client.get(
        path,
        headers={
            "Authorization": (
                "Bearer "
                + _token(
                    "44444444-4444-4444-4444-444444444444",
                    "planner",
                )
            )
        },
    )

    assert viewer_read.status_code == 200
    assert viewer_write.status_code == 403
    assert cross_tenant.status_code == 403


def test_openapi_locks_bounded_replay_resources_and_public_headers() -> None:
    document = create_planner_app({}).openapi()
    paths = document["paths"]
    schemas = document["components"]["schemas"]
    base = "/v1/tenants/{tenant_id}/replay-runs"

    assert {path for path in paths if path.startswith(base)} == {
        base,
        base + "/capabilities",
        base + "/universes",
        base + "/{replay_id}",
        base + "/{replay_id}/lineage",
        base + "/{replay_id}/exclusions",
        base + "/{replay_id}/cohorts",
    }
    assert set(paths[base]) == {"get", "post"}
    assert set(paths[base + "/universes"]) == {"get"}
    universe = schemas["ReplayUniverseMetadata"]
    assert universe["additionalProperties"] is False
    assert universe["properties"]["universe_ref"]["maxLength"] == 256
    assert universe["properties"]["universe_id"]["maxLength"] == 256
    assert "trusted_input_sha256" not in universe["properties"]
    page = schemas["ReplayUniversePage"]
    assert page["properties"]["items"]["maxItems"] == 100
    request = schemas["CreateReplayRunRequest"]
    assert request["additionalProperties"] is False
    assert "pattern" in request["properties"]["match_tolerance"]["anyOf"][1]
    run = schemas["ReplayRunView"]
    assert {
        "universe_decisions",
        "exclusions",
        "observation_lineage",
        "cohorts",
        "source_snapshot_hashes",
        "planning_fingerprints",
    }.isdisjoint(run["properties"])
