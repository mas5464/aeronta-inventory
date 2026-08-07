from __future__ import annotations

import json
import logging

from trax_io_spine.bff.planning_telemetry import PlanningTelemetry


def test_planning_telemetry_emits_bounded_structured_operational_logs(
    caplog,
) -> None:
    telemetry = PlanningTelemetry()
    caplog.set_level(
        logging.INFO,
        logger="trax_io_spine.planning.telemetry",
    )

    telemetry.observe_request(
        operation="submit",
        outcome="success",
        duration_ms=12.5,
    )
    telemetry.observe_run(
        status="completed",
        stale=True,
        solver_termination="not_proven",
    )
    telemetry.observe_submission(created=True)

    records = {
        record.event: record
        for record in caplog.records
        if hasattr(record, "event")
    }
    assert records["planning_http_request"].operation == "submit"
    assert records["planning_http_request"].outcome == "success"
    assert records["planning_http_request"].duration_ms == 12.5
    assert records["planning_run_observed"].status == "completed"
    assert records["planning_run_observed"].stale is True
    assert records["planning_run_observed"].solver_termination == "not_proven"
    assert records["planning_submission"].outcome == "created"
    assert json.loads(
        records["planning_http_request"].getMessage()
    ) == {
        "duration_ms": 12.5,
        "event": "planning_http_request",
        "operation": "submit",
        "outcome": "success",
    }
    assert json.loads(
        records["planning_run_observed"].getMessage()
    )["solver_termination"] == "not_proven"


def test_planning_telemetry_collapses_untrusted_labels_and_nonfinite_duration(
    caplog,
) -> None:
    telemetry = PlanningTelemetry()
    caplog.set_level(
        logging.INFO,
        logger="trax_io_spine.planning.telemetry",
    )
    secret = "tenant-a/run-123/decision-PN-SECRET"

    telemetry.observe_request(
        operation=secret,
        outcome=secret,
        duration_ms=float("inf"),
    )
    telemetry.observe_run(
        status=secret,
        stale=None,
        solver_termination=secret,
    )

    snapshot = telemetry.snapshot()
    assert snapshot.counters == {
        "planning_http_requests_total:unknown:unknown": 1,
        "planning_runs_observed_total:unknown": 1,
        "planning_solver_termination_observed_total:unknown": 1,
    }
    assert snapshot.durations_ms["unknown"]["total_ms"] == 0
    serialized = "\n".join(
        repr(record.__dict__) for record in caplog.records
    )
    assert secret not in serialized
