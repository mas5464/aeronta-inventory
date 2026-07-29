from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from trax_io_reco.portfolio.identity import planning_fingerprint

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.store import PlannerStore
from trax_io_spine.bff.tenant_registry import TenantRegistry
from trax_io_spine.pg.planning import (
    PlanningRerunConfig,
    PlanningRunRecord,
    PlanningRunSelectionRecord,
    PlanningRunSubmission,
)
from trax_io_spine.planning_inputs import (
    PlanningInputSnapshot,
    planning_input_coverage,
    planning_input_model_profile,
    planning_input_source_generation_hash,
    planning_input_source_snapshot_hash,
)

_SAMPLE = (
    Path(__file__).resolve().parents[3]
    / "recommendation-engine"
    / "examples"
    / "extract_sample"
)
_NOW = datetime(2026, 7, 28, tzinfo=UTC)
_RUN_ID = "11111111-1111-1111-1111-111111111111"
_CHILD_RUN_ID = "22222222-2222-2222-2222-222222222222"
_TENANT_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_KEY = "HYD-PUMP-001@YYZ"


class _Verifier:
    def verify(self, token: str) -> dict:
        tenant_id = (
            "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
            if token == "other-tenant"
            else _TENANT_UUID
        )
        return {
            "sub": f"{token}-user",
            "tenant_id": tenant_id,
            "tenant_role": token if token in {"planner", "viewer", "admin"} else "planner",
        }


class _PlanningStore:
    def __init__(self) -> None:
        self.runs: dict[str, PlanningRunRecord] = {}
        self.submitted = []
        self.rerun_floor_overrides = []
        self.page_calls: list[dict[str, object]] = []

    def _record(
        self,
        request,
        *,
        scope_kind: str = "explicit",
        input_coverage: dict[str, int],
        source_generation_hash: str,
        run_id: str = _RUN_ID,
        parent: PlanningRunRecord | None = None,
        assumption_diff: tuple[dict, ...] = (),
    ) -> PlanningRunRecord:
        decision_keys = tuple(
            menu.frontier.decision_key for menu in request.menus
        )
        return PlanningRunRecord(
            run_id=run_id,
            planning_fingerprint=planning_fingerprint(request),
            contract_version=request.contract_version,
            parent_run_id=parent.run_id if parent is not None else None,
            parent_planning_fingerprint=(
                parent.planning_fingerprint if parent is not None else None
            ),
            parent_source_snapshot_hash=(
                parent.source_snapshot_hash if parent is not None else None
            ),
            assumption_diff=assumption_diff,
            status="queued",
            scope_kind=scope_kind,
            scope_preview=decision_keys[:10],
            source_snapshot_hash=request.source_snapshot_hash,
            explicit_scope=decision_keys if scope_kind == "explicit" else (),
            source_generation_hash=source_generation_hash,
            key_count=len(request.menus),
            menu_count=len(request.menus),
            menus_fingerprint="planning_menus_" + ("b" * 64),
            candidate_count=sum(
                len(menu.frontier.candidates) for menu in request.menus
            ),
            feasible_candidate_count=sum(
                candidate.feasible
                for menu in request.menus
                for candidate in menu.frontier.candidates
            ),
            coverage={
                "scope_key_count": input_coverage["total_key_count"],
                "optimized_key_count": len(request.menus),
                "candidate_menu_key_count": len(request.menus),
                "missing_candidate_frontier_key_count": input_coverage[
                    "missing_frontier_key_count"
                ],
                "skipped_key_count": input_coverage[
                    "missing_frontier_key_count"
                ],
                "skipped_reason_counts": {
                    "missing_candidate_frontier": input_coverage[
                        "missing_frontier_key_count"
                    ]
                },
                "candidate_count": sum(
                    len(menu.frontier.candidates) for menu in request.menus
                ),
                "feasible_candidate_count": sum(
                    candidate.feasible
                    for menu in request.menus
                    for candidate in menu.frontier.candidates
                ),
                "candidate_menu_coverage_rate": str(
                    Decimal(len(request.menus))
                    / Decimal(input_coverage["total_key_count"])
                ),
                "criticality_known_key_count": input_coverage[
                    "criticality_known_key_count"
                ],
                "criticality_unknown_key_count": input_coverage[
                    "criticality_unknown_key_count"
                ],
                "repair_model_key_count": 0,
                "repair_model_coverage_rate": "0",
                "repair_credit_key_count": 0,
                "repair_credit_coverage_rate": "0",
                "low_confidence_key_count": 0,
                "minimum_candidate_confidence": None,
                "tat_confidence_status": "unavailable",
            },
            budget=request.budget,
            horizon_days=request.horizon_days,
            currency=request.currency,
            model_profile={
                "tenant_policy_version": request.tenant_policy_version,
                "forecast_version": request.forecast_version,
                "repair_model_version": request.repair_model_version,
                "candidate_planner_version": request.candidate_planner_version,
                "objective_weights": request.objective_weights.model_dump(
                    mode="json"
                ),
                "optimizer_version": request.optimizer_version,
            },
            request=request.model_dump(mode="json", exclude={"menus"}),
            advisory_only=True,
            progress_completed=0,
            progress_total=len(request.menus),
            summary=None,
            result=None,
            detail={},
            solver=None,
            warnings=(),
            skipped_keys=(
                (
                    {
                        "reason_code": "missing_candidate_frontier",
                        "count": input_coverage[
                            "missing_frontier_key_count"
                        ],
                    },
                )
                if input_coverage["missing_frontier_key_count"]
                else ()
            ),
            submitted_by="planner-user",
            warning_count=0,
            skipped_key_count=input_coverage["missing_frontier_key_count"],
            attempts=0,
            claimed_at=None,
            started_at=None,
            finished_at=None,
            created_at=_NOW,
            updated_at=_NOW,
        )

    def submit(
        self,
        request,
        parent_run_id=None,
        *,
        scope_kind: str = "explicit",
        input_coverage: dict[str, int] | None = None,
        source_generation_hash: str | None = None,
        rerun_mandatory_floors=None,
    ) -> PlanningRunSubmission:
        assert input_coverage is not None
        assert source_generation_hash is not None
        assert rerun_mandatory_floors is not None
        self.rerun_floor_overrides.append(rerun_mandatory_floors)
        self.submitted.append(
            (request, parent_run_id, scope_kind, input_coverage)
        )
        fingerprint = planning_fingerprint(request)
        existing = next(
            (
                run
                for run in self.runs.values()
                if run.planning_fingerprint == fingerprint
                and run.source_generation_hash == source_generation_hash
            ),
            None,
        )
        if existing is not None:
            return PlanningRunSubmission(run=existing, created=False)
        parent = (
            self.runs.get(parent_run_id)
            if parent_run_id is not None
            else None
        )
        if parent_run_id is not None and parent is None:
            raise ValueError("parent does not exist")
        if parent is not None and parent.status not in {
            "completed",
            "infeasible",
            "failed",
        }:
            raise ValueError("parent is not terminal")
        changes = []
        if parent is not None and parent.budget != request.budget:
            changes.append(
                {
                    "field": "budget",
                    "before": str(parent.budget),
                    "after": str(request.budget),
                }
            )
        parent_repair = (
            parent.model_profile.get("repair_model_version")
            if parent is not None
            else None
        )
        if parent is not None and parent_repair != request.repair_model_version:
            changes.append(
                {
                    "field": "repair_model_version",
                    "before": str(parent_repair),
                    "after": request.repair_model_version,
                }
            )
        run = self._record(
            request,
            scope_kind=scope_kind,
            input_coverage=input_coverage,
            source_generation_hash=source_generation_hash,
            run_id=_RUN_ID if not self.runs else _CHILD_RUN_ID,
            parent=parent,
            assumption_diff=tuple(changes),
        )
        self.runs[run.run_id] = run
        return PlanningRunSubmission(run=run, created=True)

    def get(self, run_id: str) -> PlanningRunRecord | None:
        return self.runs.get(run_id)

    def list_recent(self, *, limit: int = 20) -> tuple[PlanningRunRecord, ...]:
        return tuple(self.runs.values())[:limit]

    def rerun_config(self, run_id: str) -> PlanningRerunConfig | None:
        run = self.runs.get(run_id)
        if run is None:
            return None
        if run.status not in {"completed", "infeasible", "failed"}:
            raise ValueError("parent must be terminal")
        return PlanningRerunConfig(
            run_id=run.run_id,
            scope_kind=run.scope_kind,
            explicit_scope=run.explicit_scope,
            budget=run.budget,
            horizon_days=run.horizon_days,
            currency=run.currency,
            source_generation_hash=run.source_generation_hash,
            model_profile=run.model_profile,
            objective_weights=run.request["objective_weights"],
            mandatory_floors=self.rerun_floor_overrides[-1],
            time_limit_seconds=run.request["time_limit_seconds"],
        )

    def selections(
        self,
        run_id: str,
    ) -> tuple[PlanningRunSelectionRecord, ...]:
        if run_id not in self.runs:
            return ()
        return (
            PlanningRunSelectionRecord(
                decision_key=_KEY,
                current_candidate_id="candidate-current",
                selected_candidate_id="candidate-selected",
                selected_is_no_change=False,
                acquisition_cash=Decimal("1250"),
                objective=Decimal("8.75"),
                selection={"decision_key": _KEY},
                detail={"floor_states": []},
            ),
        )

    def selection_page(
        self,
        run_id: str,
        *,
        limit: int,
        offset: int,
        decision_key: str | None = None,
        selected_is_no_change: bool | None = None,
    ) -> tuple[tuple[PlanningRunSelectionRecord, ...], int]:
        self.page_calls.append(
            {
                "run_id": run_id,
                "limit": limit,
                "offset": offset,
                "decision_key": decision_key,
                "selected_is_no_change": selected_is_no_change,
            }
        )
        rows = self.selections(run_id)
        if decision_key is not None:
            rows = tuple(
                row for row in rows if row.decision_key == decision_key
            )
        if selected_is_no_change is not None:
            rows = tuple(
                row
                for row in rows
                if row.selected_is_no_change is selected_is_no_change
            )
        ordered = tuple(sorted(rows, key=lambda row: row.decision_key))
        return ordered[offset : offset + limit], len(ordered)


class _PlanningStoreFactory:
    def __init__(self, store: _PlanningStore) -> None:
        self.store = store
        self.bindings: list[tuple[str, str]] = []

    def __call__(self, *, principal: str, role: str) -> _PlanningStore:
        self.bindings.append((principal, role))
        return self.store


def _planner_store() -> PlannerStore:
    return PlannerStore.from_extract(
        tenant_id="acme",
        extract_dir=str(_SAMPLE),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )


class _VersionedRepairPlanner:
    def __init__(self, repair_version: str) -> None:
        self._delegate = _planner_store()
        self.repair_version = repair_version

    def _contexts(self, keys):
        snapshot = self._delegate.planning_input_snapshot(keys)
        digest = hashlib.sha256(self.repair_version.encode()).hexdigest()
        contexts = []
        for context in snapshot.contexts:
            frontier = context.candidate_frontier
            if frontier is None:
                contexts.append(context)
                continue
            candidates = tuple(
                candidate.model_copy(
                    update={
                        "model_identity": candidate.model_identity.model_copy(
                            update={
                                "repair_model": "repair-return",
                                "repair_version": self.repair_version,
                            }
                        )
                    }
                )
                for candidate in frontier.candidates
            )
            contexts.append(
                context.model_copy(
                    update={
                        "candidate_frontier": frontier.model_copy(
                            update={
                                "frontier_fingerprint": f"frontier_{digest}",
                                "output_digest": f"output_{digest}",
                                "candidates": candidates,
                            }
                        )
                    }
                )
            )
        return tuple(contexts), snapshot.coverage

    def planning_input_snapshot(self, keys=None) -> PlanningInputSnapshot:
        contexts, coverage = self._contexts(keys)
        source_snapshot_hash = planning_input_source_snapshot_hash(
            contexts,
            coverage=coverage if keys is None else None,
        )
        generation_digest = hashlib.sha256(
            f"generation:{self.repair_version}".encode()
        ).hexdigest()
        return PlanningInputSnapshot(
            contexts=contexts,
            source_snapshot_hash=source_snapshot_hash,
            source_generation_hash=f"planning_generation_{generation_digest}",
            coverage=coverage,
            seeded_at=None,
        )

    def current_planning_source_snapshot_hash(self) -> str:
        return self.planning_input_snapshot().source_snapshot_hash

    def current_planning_source_generation_hash(self) -> str:
        return self.planning_input_snapshot().source_generation_hash

    def current_planning_model_profile(self) -> dict[str, str]:
        contexts, _coverage = self._contexts(
            (("HYD-PUMP-001", "YYZ"),)
        )
        return planning_input_model_profile(contexts)


class _SingleKeyAllEligiblePlanner(_VersionedRepairPlanner):
    """Keep the all-eligible rerun fixture small and internally consistent."""

    def planning_input_snapshot(self, keys=None) -> PlanningInputSnapshot:
        requested = (
            (("HYD-PUMP-001", "YYZ"),)
            if keys is None
            else keys
        )
        contexts, _coverage = self._contexts(requested)
        coverage = planning_input_coverage(
            contexts,
            total_key_count=len(contexts),
            returned_key_count=len(contexts),
        )
        source_snapshot_hash = planning_input_source_snapshot_hash(
            contexts,
            coverage=coverage if keys is None else None,
        )
        generation_digest = hashlib.sha256(
            f"generation:{self.repair_version}".encode()
        ).hexdigest()
        return PlanningInputSnapshot(
            contexts=contexts,
            source_snapshot_hash=source_snapshot_hash,
            source_generation_hash=f"planning_generation_{generation_digest}",
            coverage=coverage,
            seeded_at=None,
        )


def _client(
    *,
    enabled: bool = True,
    planner_store: object | None = None,
) -> tuple[TestClient, _PlanningStore, _PlanningStoreFactory]:
    planning = _PlanningStore()
    factory = _PlanningStoreFactory(planning)
    app = create_planner_app(
        {"acme": planner_store or _planner_store()},
        verifier=_Verifier(),
        tenant_uuids={"acme": _TENANT_UUID},
        planning_stores={"acme": factory},
        planning_enabled_for={"acme": enabled},
    )
    return TestClient(app), planning, factory


def _headers(role: str = "planner") -> dict[str, str]:
    return {"Authorization": f"Bearer {role}"}


def _body(**updates) -> dict:
    body = {
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
        "mandatory_floors": {
            _KEY: [
                {
                    "floor_id": "tier-3-service",
                    "source": "tenant-policy-v7",
                    "min_service_level": "0.9",
                    "detail": "Protect the tenant service commitment.",
                }
            ]
        },
        "time_limit_seconds": 15,
    }
    body.update(updates)
    return body


def test_submit_builds_an_authoritative_immutable_request_and_is_idempotent() -> None:
    client, planning, factory = _client()

    first = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(),
    )
    second = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(),
    )

    assert first.status_code == 201
    assert first.json()["created"] is True
    assert first.json()["run"]["status"] == "queued"
    assert first.json()["run"]["advisory_only"] is True
    assert first.json()["run"]["stale"] is False
    assert second.status_code == 201
    assert second.json()["created"] is False
    assert second.json()["run"]["run_id"] == first.json()["run"]["run_id"]
    solve_request, parent_run_id, scope_kind, input_coverage = (
        planning.submitted[0]
    )
    assert parent_run_id is None
    assert scope_kind == "explicit"
    assert input_coverage["total_key_count"] == 1
    assert solve_request.tenant_id == "acme"
    assert solve_request.budget == Decimal("5000")
    assert solve_request.horizon_days == 60
    assert solve_request.source_snapshot_hash.startswith("candidate_snapshot_")
    assert [menu.frontier.decision_key for menu in solve_request.menus] == [_KEY]
    assert solve_request.menus[0].mandatory_floors[0].floor_id == "tier-3-service"
    assert solve_request.objective_weights.shortage_reduction_weight == Decimal("2")
    assert factory.bindings[:2] == [
        ("planner-user", "planner"),
        ("planner-user", "planner"),
    ]


def test_saved_rerun_uses_current_trusted_repair_model_and_parent_lineage() -> None:
    planner = _VersionedRepairPlanner("repair-return-v1")
    client, planning, _factory = _client(planner_store=planner)
    parent = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(),
    )
    assert parent.status_code == 201
    planning.runs[_RUN_ID] = planning.runs[_RUN_ID].model_copy(
        update={"status": "completed"}
    )

    saved = client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}/rerun-config",
        headers=_headers("viewer"),
    )
    assert saved.status_code == 200
    assert saved.json()["keys"] == [
        {"pn": "HYD-PUMP-001", "location": "YYZ"}
    ]
    assert saved.json()["budget"] == "5000"
    assert (
        saved.json()["parent_model_profile"]["repair_model_version"]
        == "repair-return-v1"
    )
    assert saved.json()["repair_assumption_change_available"] is False

    unchanged_body = {
        "scope_kind": saved.json()["scope_kind"],
        "keys": saved.json()["keys"],
        "budget": saved.json()["budget"],
        "horizon_days": saved.json()["horizon_days"],
        "currency": saved.json()["currency"],
        "objective_weights": saved.json()["objective_weights"],
        "mandatory_floors": saved.json()["mandatory_floors"],
        "time_limit_seconds": saved.json()["time_limit_seconds"],
        "parent_run_id": _RUN_ID,
    }
    unchanged = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=unchanged_body,
    )
    assert unchanged.status_code == 201
    assert unchanged.json()["created"] is False
    assert unchanged.json()["run"]["run_id"] == _RUN_ID

    planner.repair_version = "repair-return-v2"
    changed_config = client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}/rerun-config",
        headers=_headers("viewer"),
    )
    assert changed_config.status_code == 200
    assert (
        changed_config.json()["current_trusted_model_profile"][
            "repair_model_version"
        ]
        == "repair-return-v2"
    )
    assert changed_config.json()["repair_assumption_change_available"] is True

    changed_body = unchanged_body
    child = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=changed_body,
    )

    assert child.status_code == 201
    assert child.json()["created"] is True
    assert child.json()["run"]["run_id"] == _CHILD_RUN_ID
    assert child.json()["run"]["parent_run_id"] == _RUN_ID
    assert child.json()["run"]["planning_fingerprint"] != parent.json()["run"][
        "planning_fingerprint"
    ]
    assert {
        change["field"] for change in child.json()["run"]["assumption_diff"]
    } == {"repair_model_version"}
    solve_request, parent_id, _scope, _coverage = planning.submitted[-1]
    assert parent_id == _RUN_ID
    assert solve_request.repair_model_version == "repair-return-v2"


def test_all_eligible_rerun_config_never_expands_automatic_menu_floors() -> None:
    client, planning, _factory = _client(
        planner_store=_SingleKeyAllEligiblePlanner("repair-return-v1")
    )
    submitted = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(
            scope_kind="all_eligible",
            keys=[],
            mandatory_floors={},
        ),
    )
    assert submitted.status_code == 201
    assert planning.rerun_floor_overrides == [{}]
    planning.runs[_RUN_ID] = planning.runs[_RUN_ID].model_copy(
        update={"status": "completed"}
    )
    planning.automatic_menu_floor_count = 11_800

    config = client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}/rerun-config",
        headers=_headers("viewer"),
    )

    assert config.status_code == 200
    assert config.json()["scope_kind"] == "all_eligible"
    assert config.json()["keys"] == []
    assert config.json()["mandatory_floors"] == {}
    assert len(config.content) < 10_000


def test_rerun_config_requires_a_terminal_same_tenant_parent() -> None:
    client, _planning, _factory = _client()
    submitted = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(),
    )
    assert submitted.status_code == 201

    active = client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}/rerun-config",
        headers=_headers("viewer"),
    )
    missing = client.get(
        "/v1/tenants/acme/planning-runs/"
        "33333333-3333-3333-3333-333333333333/rerun-config",
        headers=_headers("viewer"),
    )

    assert active.status_code == 409
    assert (
        active.json()["detail"]["code"]
        == "planning_rerun_parent_not_terminal"
    )
    assert missing.status_code == 404


def test_all_eligible_scope_is_server_resolved_without_browser_keys() -> None:
    delegate = _planner_store()

    class _AuthoritativeScope:
        keys = [
            ("HYD-PUMP-001", "YOW"),
            ("HYD-PUMP-001", "YYZ"),
        ]

        def part_context(self, pn: str, location: str):
            return delegate.part_context(pn, location)

        def planning_input_snapshot(
            self,
            keys: tuple[tuple[str, str], ...] | None = None,
        ) -> PlanningInputSnapshot:
            requested = tuple(self.keys) if keys is None else keys
            contexts = tuple(
                self.part_context(pn, location) for pn, location in requested
            )
            coverage = planning_input_coverage(
                contexts,
                total_key_count=len(contexts),
                returned_key_count=len(contexts),
            )
            source_snapshot_hash = planning_input_source_snapshot_hash(
                contexts,
                coverage=coverage if keys is None else None,
            )
            return PlanningInputSnapshot(
                contexts=contexts,
                source_snapshot_hash=source_snapshot_hash,
                source_generation_hash=planning_input_source_generation_hash(
                    source_snapshot_hash
                ),
                coverage=coverage,
                seeded_at=None,
            )

    client, planning, _factory = _client(planner_store=_AuthoritativeScope())

    response = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(
            scope_kind="all_eligible",
            keys=[],
        ),
    )

    assert response.status_code == 201
    solve_request, _parent, scope_kind, input_coverage = planning.submitted[0]
    assert scope_kind == "all_eligible"
    assert input_coverage["total_key_count"] == 2
    assert [
        menu.frontier.decision_key for menu in solve_request.menus
    ] == ["HYD-PUMP-001@YOW", "HYD-PUMP-001@YYZ"]
    assert solve_request.source_snapshot_hash.startswith("candidate_snapshot_")
    assert len({solve_request.source_snapshot_hash}) == 1


def test_all_eligible_discloses_authoritative_missing_frontier_coverage() -> None:
    delegate = _planner_store()
    eligible = delegate.part_context("HYD-PUMP-001", "YYZ")
    missing_template = delegate.part_context("HYD-PUMP-001", "YOW").model_copy(
        update={"candidate_frontier": None}
    )
    missing = tuple(
        missing_template.model_copy(
            update={"pn": f"MISSING-{index}", "location": "YYZ"}
        )
        for index in range(3)
    )

    class _CoverageScope:
        def planning_input_snapshot(self, keys=None) -> PlanningInputSnapshot:
            assert keys is None
            all_contexts = (eligible, *missing)
            optimized = (eligible,)
            coverage = planning_input_coverage(
                all_contexts,
                total_key_count=4,
                returned_key_count=1,
            )
            source_snapshot_hash = planning_input_source_snapshot_hash(
                all_contexts,
                coverage=coverage,
            )
            return PlanningInputSnapshot(
                contexts=optimized,
                source_snapshot_hash=source_snapshot_hash,
                source_generation_hash=planning_input_source_generation_hash(
                    source_snapshot_hash
                ),
                coverage=coverage,
                seeded_at=None,
            )

    client, planning, _factory = _client(planner_store=_CoverageScope())
    response = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(scope_kind="all_eligible", keys=[]),
    )

    assert response.status_code == 201
    run = response.json()["run"]
    assert run["coverage"]["authoritative_key_count"] == 4
    assert run["coverage"]["eligible_key_count"] == 1
    assert run["coverage"]["missing_candidate_frontier_key_count"] == 3
    assert run["coverage"]["candidate_menu_coverage_rate"] == "0.25"
    assert run["skipped_keys"] == {
        "total": 3,
        "counted_items": 3,
        "by_code": [{"code": "missing_candidate_frontier", "count": 3}],
        "code_list_truncated": False,
    }
    assert planning.submitted[0][3]["missing_frontier_key_count"] == 3

    stored = planning.runs[_RUN_ID]
    planning.runs[_RUN_ID] = stored.model_copy(
        update={
            "skipped_keys": (
                {
                    "reason_code": "missing_candidate_frontier",
                    "count": 3,
                },
                {"reason_code": "worker_ineligible", "count": 2},
            ),
            "skipped_key_count": 5,
        }
    )
    detail = client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}",
        headers=_headers("viewer"),
    )
    assert detail.json()["skipped_keys"] == {
        "total": 5,
        "counted_items": 5,
        "by_code": [
            {"code": "missing_candidate_frontier", "count": 3},
            {"code": "worker_ineligible", "count": 2},
        ],
        "code_list_truncated": False,
    }


def test_all_eligible_rejects_client_keys_and_explicit_scope_cannot_cross_universe() -> None:
    client, planning, _factory = _client()

    smuggled = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(
            scope_kind="all_eligible",
            keys=[{"pn": "FOREIGN", "location": "OTHER"}],
            mandatory_floors={},
        ),
    )
    foreign_explicit = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(
            scope_kind="explicit",
            keys=[{"pn": "FOREIGN", "location": "OTHER"}],
            mandatory_floors={},
        ),
    )

    assert smuggled.status_code == 422
    assert (
        smuggled.json()["detail"]["code"]
        == "planning_request_invalid"
    )
    assert foreign_explicit.status_code == 422
    assert (
        foreign_explicit.json()["detail"]["code"]
        == "planning_input_not_found"
    )
    assert planning.submitted == []


def test_solver_limit_stays_below_worker_lease() -> None:
    client, planning, _factory = _client()

    rejected = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(time_limit_seconds=601),
    )

    assert rejected.status_code == 422
    assert planning.submitted == []


def test_submission_bounds_scope_floor_fields_counts_and_parent_identity() -> None:
    client, planning, _factory = _client()
    too_many_floor_keys = {
        f"PN-{index:03d}@YYZ": [
            {
                "floor_id": f"floor-{index}",
                "source": "planner-input",
                "max_aog_risk": "0.1",
            }
        ]
        for index in range(201)
    }
    too_many_for_one_key = [
        {
            "floor_id": f"floor-{index}",
            "source": "planner-input",
            "max_aog_risk": "0.1",
        }
        for index in range(21)
    ]

    responses = [
        client.post(
            "/v1/tenants/acme/planning-runs",
            headers=_headers(),
            json=_body(
                keys=[{"pn": "P" * 129, "location": "YYZ"}],
                mandatory_floors={},
            ),
        ),
        client.post(
            "/v1/tenants/acme/planning-runs",
            headers=_headers(),
            json=_body(mandatory_floors=too_many_floor_keys),
        ),
        client.post(
            "/v1/tenants/acme/planning-runs",
            headers=_headers(),
            json=_body(mandatory_floors={_KEY: too_many_for_one_key}),
        ),
        client.post(
            "/v1/tenants/acme/planning-runs",
            headers=_headers(),
            json=_body(
                mandatory_floors={
                    _KEY: [
                        {
                            "floor_id": "f" * 129,
                            "source": "planner-input",
                            "max_aog_risk": "0.1",
                        }
                    ]
                }
            ),
        ),
        client.post(
            "/v1/tenants/acme/planning-runs",
            headers=_headers(),
            json=_body(parent_run_id="not-a-uuid"),
        ),
    ]

    assert [response.status_code for response in responses] == [422] * 5
    assert planning.submitted == []


def test_outside_scope_floor_error_uses_a_bounded_sample() -> None:
    client, planning, _factory = _client()
    floors = {
        f"OUTSIDE-{index:02d}@YYZ": [
            {
                "floor_id": f"floor-{index}",
                "source": "planner-input",
                "max_aog_risk": "0.1",
            }
        ]
        for index in range(12)
    }

    response = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(mandatory_floors=floors),
    )

    assert response.status_code == 422
    message = response.json()["detail"]["message"]
    assert "(+2 more)" in message
    assert "OUTSIDE-09@YYZ" in message
    assert "OUTSIDE-10@YYZ" not in message
    assert "OUTSIDE-11@YYZ" not in message
    assert len(message) < 300
    assert planning.submitted == []


def test_submission_conflict_redacts_store_and_lineage_details() -> None:
    client, planning, _factory = _client()

    def conflicting(*_args, **_kwargs):
        raise ValueError(
            "tenant aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa "
            "planning_secret database driver failure"
        )

    planning.submit = conflicting  # type: ignore[method-assign]
    response = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "planning_submission_conflict",
        "message": (
            "The immutable planning run conflicts with existing lineage or "
            "tenant-scoped submission state."
        ),
        "retryable": False,
    }
    assert "aaaaaaaa" not in response.text
    assert "planning_secret" not in response.text
    assert "driver" not in response.text


def test_viewer_reads_history_detail_and_selections_but_cannot_submit() -> None:
    client, _planning, factory = _client()
    submitted = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(),
    )
    assert submitted.status_code == 201

    history = client.get(
        "/v1/tenants/acme/planning-runs?limit=5",
        headers=_headers("viewer"),
    )
    detail = client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}",
        headers=_headers("viewer"),
    )
    selections = client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}/selections",
        headers=_headers("viewer"),
    )
    forbidden = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers("viewer"),
        json=_body(),
    )

    assert history.status_code == 200
    assert [run["run_id"] for run in history.json()] == [_RUN_ID]
    assert detail.status_code == 200
    assert detail.json()["scope"] == {
        "kind": "explicit",
        "key_count": 1,
        "preview_keys": [_KEY],
        "preview_truncated": False,
    }
    assert selections.status_code == 200
    assert selections.json()["total"] == 1
    assert selections.json()["items"][0]["decision_key"] == _KEY
    assert forbidden.status_code == 403
    assert ("viewer-user", "viewer") in factory.bindings


def test_capability_is_tenant_scoped_default_off_and_never_grants_authority() -> None:
    enabled_client, _planning, _factory = _client()
    disabled_client, disabled_store, _disabled_factory = _client(enabled=False)

    planner_capability = enabled_client.get(
        "/v1/tenants/acme/planning-runs/capabilities",
        headers=_headers(),
    )
    viewer_capability = enabled_client.get(
        "/v1/tenants/acme/planning-runs/capabilities",
        headers=_headers("viewer"),
    )
    disabled_capability = disabled_client.get(
        "/v1/tenants/acme/planning-runs/capabilities",
        headers=_headers(),
    )
    disabled_read = disabled_client.get(
        "/v1/tenants/acme/planning-runs",
        headers=_headers("viewer"),
    )
    disabled_write = disabled_client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(),
    )

    assert planner_capability.json() == {
        "contract_version": "planning-capability.v1",
        "enabled": True,
        "advisory_only": True,
        "can_read": True,
        "can_submit": True,
        "reason_code": "enabled",
    }
    assert viewer_capability.json()["can_read"] is True
    assert viewer_capability.json()["can_submit"] is False
    assert viewer_capability.json()["reason_code"] == "insufficient_role"
    assert disabled_capability.json()["enabled"] is False
    assert disabled_capability.json()["can_read"] is False
    assert disabled_read.status_code == 404
    assert disabled_read.json()["detail"]["code"] == "planning_feature_disabled"
    assert disabled_write.status_code == 404
    assert disabled_store.submitted == []


def test_detail_discloses_current_input_staleness_without_mutating_history() -> None:
    planner = _planner_store()
    planner.planning_source_snapshot_hash = (  # type: ignore[attr-defined]
        lambda: "candidate_snapshot_current"
    )
    planner.planning_source_generation_hash = (  # type: ignore[attr-defined]
        lambda: "planning_generation_current"
    )
    client, planning, _factory = _client(planner_store=planner)
    response = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(),
    )
    assert response.status_code == 201
    original = planning.runs[_RUN_ID]
    planning.runs[_RUN_ID] = original.model_copy(
        update={
            "scope_kind": "all_eligible",
            "source_snapshot_hash": "candidate_snapshot_old",
            "source_generation_hash": "planning_generation_old",
        }
    )

    detail = client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}",
        headers=_headers("viewer"),
    )

    assert detail.status_code == 200
    assert detail.json()["stale"] is True
    assert (
        detail.json()["current_source_snapshot_hash"]
        == "candidate_snapshot_current"
    )
    assert (
        detail.json()["current_source_generation_hash"]
        == "planning_generation_current"
    )
    assert "immutable submitted snapshot" in detail.json()["stale_reason"]
    assert planning.runs[_RUN_ID].source_snapshot_hash == "candidate_snapshot_old"


def test_explicit_subset_uses_the_common_generation_for_staleness() -> None:
    planner = _planner_store()
    client, _planning, _factory = _client(planner_store=planner)
    submitted = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(),
    )
    planner.current_planning_source_generation_hash = (  # type: ignore[method-assign]
        lambda: "planning_generation_new"
    )
    detail = client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}",
        headers=_headers("viewer"),
    )

    assert submitted.status_code == 201
    assert detail.status_code == 200
    assert detail.json()["scope"]["kind"] == "explicit"
    assert detail.json()["stale"] is True
    assert detail.json()["current_source_snapshot_hash"] is None
    assert (
        detail.json()["current_source_generation_hash"]
        == "planning_generation_new"
    )


def test_selection_filter_pagination_is_stable_without_changing_run_aggregates() -> None:
    client, planning, _factory = _client()
    submitted = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(),
    )
    original_key_count = submitted.json()["run"]["key_count"]

    changed = client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}/selections"
        "?limit=1&offset=0&selected_is_no_change=false",
        headers=_headers("viewer"),
    )
    no_change = client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}/selections"
        "?selected_is_no_change=true",
        headers=_headers("viewer"),
    )
    exact_miss = client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}/selections"
        "?decision_key=OTHER%40YYZ",
        headers=_headers("viewer"),
    )
    detail = client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}",
        headers=_headers("viewer"),
    )

    assert changed.json()["items"][0]["decision_key"] == _KEY
    assert changed.json()["total"] == 1
    assert changed.json()["limit"] == 1
    assert no_change.json()["items"] == []
    assert no_change.json()["total"] == 0
    assert exact_miss.json()["items"] == []
    assert detail.json()["key_count"] == original_key_count
    assert planning.page_calls[:3] == [
        {
            "run_id": _RUN_ID,
            "limit": 1,
            "offset": 0,
            "decision_key": None,
            "selected_is_no_change": False,
        },
        {
            "run_id": _RUN_ID,
            "limit": 50,
            "offset": 0,
            "decision_key": None,
            "selected_is_no_change": True,
        },
        {
            "run_id": _RUN_ID,
            "limit": 50,
            "offset": 0,
            "decision_key": "OTHER@YYZ",
            "selected_is_no_change": None,
        },
    ]


def test_run_resource_ids_and_selection_offsets_are_bounded() -> None:
    client, planning, _factory = _client()
    for suffix in (
        "not-a-uuid",
        "not-a-uuid/rerun-config",
        "not-a-uuid/selections",
    ):
        response = client.get(
            f"/v1/tenants/acme/planning-runs/{suffix}",
            headers=_headers("viewer"),
        )
        assert response.status_code == 422

    submitted = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(),
    )
    assert submitted.status_code == 201
    response = client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}/selections"
        "?offset=1000001",
        headers=_headers("viewer"),
    )

    assert response.status_code == 422
    assert planning.page_calls == []


def test_run_headers_never_serialize_raw_menus_scopes_or_terminal_rows() -> None:
    client, planning, _factory = _client()
    submitted = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(),
    )
    assert submitted.status_code == 201
    record = planning.runs[_RUN_ID]
    sensitive = {"secret_candidate_payload": "must-stay-server-side"}
    planning.runs[_RUN_ID] = record.model_copy(
        update={
            "explicit_scope": ("LEAK@LOC",) * 58_899,
            "request": {"menus": [sensitive] * 58_899},
            "result": {"selections": [sensitive] * 58_899},
            "detail": {"selection_details": [sensitive] * 58_899},
        }
    )

    history = client.get(
        "/v1/tenants/acme/planning-runs",
        headers=_headers("viewer"),
    )
    detail = client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}",
        headers=_headers("viewer"),
    )

    assert history.status_code == detail.status_code == 200
    for payload in (submitted.json()["run"], history.json()[0], detail.json()):
        assert {
            "request",
            "explicit_scope",
            "result",
            "selection_details",
        }.isdisjoint(payload)
    assert "secret_candidate_payload" not in history.text
    assert "secret_candidate_payload" not in detail.text
    assert len(history.content) < 20_000
    assert len(detail.content) < 20_000


def test_planning_routes_enforce_auth_tenant_and_resource_existence() -> None:
    client, _planning, _factory = _client()

    assert client.get("/v1/tenants/acme/planning-runs").status_code == 401
    assert (
        client.get(
            "/v1/tenants/acme/planning-runs",
            headers=_headers("other-tenant"),
        ).status_code
        == 403
    )
    missing_run_id = "99999999-9999-4999-8999-999999999999"
    missing = client.get(
        f"/v1/tenants/acme/planning-runs/{missing_run_id}",
        headers=_headers("viewer"),
    )
    missing_selections = client.get(
        f"/v1/tenants/acme/planning-runs/{missing_run_id}/selections",
        headers=_headers("viewer"),
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "planning_run_not_found"
    assert missing_selections.status_code == 404


def test_submit_rejects_noncanonical_scope_floor_leakage_and_horizon_mismatch() -> None:
    client, planning, _factory = _client()

    noncanonical = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(
            keys=[
                {"pn": "HYD-PUMP-001", "location": "YYZ"},
                {"pn": "FILTER-EXP-042", "location": "YYZ"},
            ],
            mandatory_floors={},
        ),
    )
    outside_floor = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(
            mandatory_floors={
                "NOT-IN-SCOPE@YYZ": [
                    {
                        "floor_id": "leak",
                        "source": "test",
                        "max_aog_risk": "0.1",
                    }
                ]
            }
        ),
    )
    wrong_horizon = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(horizon_days=30),
    )

    assert noncanonical.status_code == 422
    assert (
        noncanonical.json()["detail"]["code"]
        == "planning_request_invalid"
    )
    assert outside_floor.status_code == 422
    assert "outside the resolved scope" in outside_floor.text
    assert wrong_horizon.status_code == 422
    assert "candidate horizon 60" in wrong_horizon.text
    assert planning.submitted == []


def test_submit_rejects_excessive_numeric_precision_and_horizon() -> None:
    client, planning, _factory = _client()
    weights = _body()["objective_weights"]
    floors = _body()["mandatory_floors"]
    cases = (
        _body(budget="1e1000000"),
        _body(budget="12345678901234567.89"),
        _body(
            objective_weights={
                **weights,
                "shortage_reduction_weight": "0.1234567",
            }
        ),
        _body(
            mandatory_floors={
                _KEY: [
                    {
                        **floors[_KEY][0],
                        "min_service_level": "0.1234567",
                    }
                ]
            }
        ),
        _body(horizon_days=3_651),
    )

    responses = [
        client.post(
            "/v1/tenants/acme/planning-runs",
            headers=_headers(),
            json=body,
        )
        for body in cases
    ]

    assert [response.status_code for response in responses] == [422] * len(
        cases
    )
    assert planning.submitted == []


def test_validation_failure_uses_redacted_locked_error_envelope() -> None:
    client, planning, _factory = _client()
    rejected_input = "tenant-secret-decision-key-and-payload"

    response = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(budget=rejected_input),
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "planning_request_invalid",
            "message": (
                "The planning request does not match the supported contract."
            ),
            "retryable": False,
        }
    }
    assert rejected_input not in response.text
    assert planning.submitted == []


def test_planning_telemetry_has_only_bounded_non_sensitive_labels() -> None:
    client, _planning, _factory = _client()
    response = client.post(
        "/v1/tenants/acme/planning-runs",
        headers=_headers(),
        json=_body(),
    )
    assert response.status_code == 201
    client.get(
        f"/v1/tenants/acme/planning-runs/{_RUN_ID}",
        headers=_headers("viewer"),
    )

    snapshot = client.app.state.planning_telemetry.snapshot()
    assert snapshot.counters["planning_submissions_total:created"] == 1
    assert snapshot.counters["planning_runs_observed_total:queued"] >= 2
    assert snapshot.counters["planning_http_requests_total:submit:success"] == 1
    assert snapshot.counters["planning_http_requests_total:detail:success"] == 1
    assert snapshot.durations_ms["submit"]["count"] == 1
    serialized = repr(snapshot)
    assert "acme" not in serialized
    assert _RUN_ID not in serialized
    assert "planner-user" not in serialized
    assert _KEY not in serialized


def test_registry_builds_fresh_identity_bound_planning_stores() -> None:
    pool = object()
    registry = TenantRegistry(pool)
    registry._uuids["acme"] = _TENANT_UUID

    first = registry.planning_store_for(
        "acme",
        principal="planner-a",
        role="planner",
    )
    second = registry.planning_store_for(
        "acme",
        principal="viewer-b",
        role="viewer",
    )

    assert first is not None and second is not None
    assert first is not second
    assert first.tenant_id == second.tenant_id == "acme"
    assert first._uuid == second._uuid == _TENANT_UUID
    assert first._principal == "planner-a"
    assert second._principal == "viewer-b"
    assert first._role == "planner"
    assert second._role == "viewer"
