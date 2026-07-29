"""Consumer lock for the versioned advisory planning HTTP surface."""

from trax_io_spine.bff.app import create_planner_app

_BASE = "/v1/tenants/{tenant_id}/planning-runs"


def _ref(operation: dict, status: str) -> str:
    return operation["responses"][status]["content"]["application/json"]["schema"][
        "$ref"
    ]


def test_openapi_locks_resource_routes_and_wire_models() -> None:
    document = create_planner_app({}).openapi()
    paths = document["paths"]
    schemas = document["components"]["schemas"]

    assert set(path for path in paths if path.startswith(_BASE)) == {
        _BASE,
        _BASE + "/capabilities",
        _BASE + "/{run_id}",
        _BASE + "/{run_id}/rerun-config",
        _BASE + "/{run_id}/selections",
    }
    assert set(paths[_BASE]) == {"get", "post"}
    assert _ref(paths[_BASE]["post"], "201").endswith(
        "/PlanningRunSubmissionView"
    )
    list_schema = paths[_BASE]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert list_schema["type"] == "array"
    assert list_schema["items"]["$ref"].endswith("/PlanningRunView")
    assert _ref(paths[_BASE + "/capabilities"]["get"], "200").endswith(
        "/PlanningCapability"
    )
    assert _ref(paths[_BASE + "/{run_id}"]["get"], "200").endswith(
        "/PlanningRunView"
    )
    assert _ref(
        paths[_BASE + "/{run_id}/rerun-config"]["get"],
        "200",
    ).endswith("/PlanningRerunConfigView")
    assert _ref(
        paths[_BASE + "/{run_id}/selections"]["get"],
        "200",
    ).endswith("/PlanningRunSelectionsPage")
    detail_parameters = {
        parameter["name"]: parameter
        for parameter in paths[_BASE + "/{run_id}"]["get"]["parameters"]
    }
    assert detail_parameters["run_id"]["schema"]["format"] == "uuid"
    selection_parameters = {
        parameter["name"]: parameter
        for parameter in paths[_BASE + "/{run_id}/selections"]["get"][
            "parameters"
        ]
    }
    assert selection_parameters["run_id"]["schema"]["format"] == "uuid"
    assert selection_parameters["offset"]["schema"]["maximum"] == 1_000_000.0

    submission = schemas["CreatePlanningRunRequest"]
    assert submission["additionalProperties"] is False
    assert set(submission["required"]) == {"budget", "horizon_days"}
    assert submission["properties"]["scope_kind"]["enum"] == [
        "explicit",
        "all_eligible",
    ]
    assert submission["properties"]["keys"]["maxItems"] == 200
    assert submission["properties"]["currency"]["pattern"] == "^[A-Z]{3}$"
    assert submission["properties"]["horizon_days"]["maximum"] == 3650.0
    assert "pattern" in submission["properties"]["budget"]["anyOf"][1]
    floors = submission["properties"]["mandatory_floors"]
    assert floors["maxProperties"] == 200
    assert floors["propertyNames"]["maxLength"] == 257
    floor_items = next(iter(floors["patternProperties"].values()))
    assert floor_items["maxItems"] == 20
    floor = schemas["PlanningMandatoryFloorInput-Input"]
    assert floor["additionalProperties"] is False
    assert floor["properties"]["floor_id"]["maxLength"] == 128
    assert floor["properties"]["source"]["maxLength"] == 128
    assert floor["properties"]["detail"]["anyOf"][0]["maxLength"] == 500
    assert "pattern" in floor["properties"]["min_service_level"]["anyOf"][1]
    weights = schemas["PlanningObjectiveWeightsInput"]
    assert weights["additionalProperties"] is False
    assert (
        weights["properties"]["criticality_weights"]["maxProperties"]
        == 5
    )
    assert (
        "pattern"
        in weights["properties"]["shortage_reduction_weight"]["anyOf"][1]
    )
    assert submission["properties"]["parent_run_id"]["anyOf"][0]["format"] == "uuid"
    scope_key = schemas["PlanningScopeKey"]
    assert scope_key["properties"]["pn"]["maxLength"] == 128
    assert scope_key["properties"]["location"]["maxLength"] == 128
    assert (
        submission["properties"]["time_limit_seconds"]["maximum"]
        == 600.0
    )

    run = schemas["PlanningRunView"]
    assert run["additionalProperties"] is False
    assert run["properties"]["status"]["enum"] == [
        "queued",
        "running",
        "completed",
        "infeasible",
        "failed",
    ]
    for field in (
        "source_snapshot_hash",
        "source_generation_hash",
        "scope",
        "budget",
        "horizon_days",
        "currency",
        "model_profile",
        "advisory_only",
        "progress_completed",
        "progress_total",
        "summary",
        "infeasibility",
        "detail",
        "solver",
        "warnings",
        "skipped_keys",
        "coverage",
        "stale",
        "current_source_generation_hash",
    ):
        assert field in run["properties"]
    assert {
        "request",
        "explicit_scope",
        "result",
        "selection_details",
    }.isdisjoint(run["properties"])

    evidence = schemas["PlanningEvidenceSummary"]
    assert set(evidence["required"]) == {
        "total",
        "counted_items",
        "by_code",
        "code_list_truncated",
    }
    summary_refs = {
        item.get("$ref", "").rsplit("/", maxsplit=1)[-1]
        for item in run["properties"]["summary"]["anyOf"]
    }
    assert "PortfolioSummary" in summary_refs
    summary = schemas["PortfolioSummary"]
    assert {
        "warning_count",
        "confidence_summary",
    } <= set(summary["properties"])
    confidence = schemas["PortfolioConfidenceSummary"]
    assert {
        "selected_confidence_total",
        "minimum_selected_confidence",
        "low_confidence_threshold",
        "low_confidence_key_count",
    } <= set(confidence["properties"])

    coverage = schemas["PlanningCoverage"]
    for field in (
        "authoritative_key_count",
        "eligible_key_count",
        "missing_candidate_frontier_key_count",
        "criticality_unknown_key_count",
        "candidate_menu_coverage_rate",
    ):
        assert field in coverage["properties"]

    page = schemas["PlanningRunSelectionsPage"]
    assert page["additionalProperties"] is False
    assert set(page["required"]) == {"items", "total", "limit", "offset"}
    assert page["properties"]["limit"]["maximum"] == 100.0
    assert page["properties"]["items"]["maxItems"] == 100

    rerun = schemas["PlanningRerunConfigView"]
    assert rerun["additionalProperties"] is False
    assert rerun["properties"]["keys"]["maxItems"] == 200
    assert rerun["properties"]["parent_run_id"]["format"] == "uuid"
    assert (
        rerun["properties"]["repair_assumption_mode"]["const"]
        == "current_trusted"
    )
    assert "current_trusted_model_profile" in rerun["properties"]

    capability = schemas["PlanningCapability"]
    assert capability["properties"]["advisory_only"]["const"] is True
    assert capability["properties"]["reason_code"]["enum"] == [
        "enabled",
        "feature_disabled",
        "insufficient_role",
    ]


def test_planning_surface_has_no_writeback_or_commit_operation() -> None:
    paths = create_planner_app({}).openapi()["paths"]
    planning_paths = {
        path: operations
        for path, operations in paths.items()
        if path.startswith(_BASE)
    }

    assert all(
        not any(token in path for token in ("commit", "approve", "writeback"))
        for path in planning_paths
    )
    assert all(
        method in {"get", "post"}
        for operations in planning_paths.values()
        for method in operations
    )
