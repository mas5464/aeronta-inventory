"""Low-cardinality, non-sensitive telemetry for the planning HTTP surface."""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any

from trax_io_spine.operational_logging import log_operational_event

log = logging.getLogger("trax_io_spine.planning.telemetry")

_HTTP_OPERATIONS = frozenset(
    {"capabilities", "submit", "list", "detail", "selections"}
)
_HTTP_OUTCOMES = frozenset({"success", "client_error", "server_error"})
_RUN_STATUSES = frozenset(
    {"queued", "running", "completed", "infeasible", "failed"}
)
_SOLVER_TERMINATIONS = frozenset(
    {"optimal", "not_proven", "infeasible", "failed"}
)


def _bounded_label(value: str, allowed: frozenset[str]) -> str:
    return value if value in allowed else "unknown"


def _bounded_duration(value: float) -> float:
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, duration) if math.isfinite(duration) else 0.0


def _operation(path: str, method: str) -> str | None:
    if "/planning-runs" not in path:
        return None
    suffix = path.split("/planning-runs", maxsplit=1)[1].strip("/")
    if suffix == "capabilities":
        return "capabilities"
    if not suffix:
        return "submit" if method == "POST" else "list"
    if suffix.endswith("/selections"):
        return "selections"
    return "detail"


@dataclass(frozen=True)
class PlanningTelemetrySnapshot:
    counters: dict[str, int]
    durations_ms: dict[str, dict[str, float | int]]


class PlanningTelemetry:
    """Process-local collector with deliberately bounded labels.

    Tenant identifiers, principals, decision keys, request bodies, run ids,
    and exception strings are never accepted. Production can inject a
    compatible collector through ``create_planner_app``; this default still
    provides deterministic contract tests and a useful process snapshot.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[str] = Counter()
        self._duration_count: Counter[str] = Counter()
        self._duration_total_ms: Counter[str] = Counter()
        self._duration_max_ms: dict[str, float] = {}

    def observe_request(
        self,
        *,
        operation: str,
        outcome: str,
        duration_ms: float,
    ) -> None:
        operation = _bounded_label(operation, _HTTP_OPERATIONS)
        outcome = _bounded_label(outcome, _HTTP_OUTCOMES)
        duration_ms = _bounded_duration(duration_ms)
        key = f"planning_http_requests_total:{operation}:{outcome}"
        with self._lock:
            self._counters[key] += 1
            self._duration_count[operation] += 1
            self._duration_total_ms[operation] += duration_ms
            self._duration_max_ms[operation] = max(
                self._duration_max_ms.get(operation, 0.0),
                duration_ms,
            )
        log_operational_event(
            log,
            logging.INFO,
            "planning_http_request",
            operation=operation,
            outcome=outcome,
            duration_ms=duration_ms,
        )

    def observe_run(
        self,
        *,
        status: str,
        stale: bool | None,
        solver_termination: str | None,
    ) -> None:
        status = _bounded_label(status, _RUN_STATUSES)
        termination = (
            _bounded_label(solver_termination, _SOLVER_TERMINATIONS)
            if solver_termination is not None
            else None
        )
        with self._lock:
            self._counters[f"planning_runs_observed_total:{status}"] += 1
            if stale is True:
                self._counters["planning_stale_runs_observed_total"] += 1
            if termination:
                self._counters[
                    "planning_solver_termination_observed_total:"
                    f"{termination}"
                ] += 1
        log_operational_event(
            log,
            logging.INFO,
            "planning_run_observed",
            status=status,
            stale=stale,
            solver_termination=termination,
        )

    def observe_submission(self, *, created: bool) -> None:
        outcome = "created" if created else "reused"
        with self._lock:
            self._counters[f"planning_submissions_total:{outcome}"] += 1
        log_operational_event(
            log,
            logging.INFO,
            "planning_submission",
            outcome=outcome,
        )

    def snapshot(self) -> PlanningTelemetrySnapshot:
        with self._lock:
            durations = {
                operation: {
                    "count": self._duration_count[operation],
                    "total_ms": float(self._duration_total_ms[operation]),
                    "max_ms": self._duration_max_ms.get(operation, 0.0),
                }
                for operation in sorted(self._duration_count)
            }
            return PlanningTelemetrySnapshot(
                counters=dict(sorted(self._counters.items())),
                durations_ms=durations,
            )


class PlanningTelemetryMiddleware:
    def __init__(self, app, *, telemetry: PlanningTelemetry) -> None:
        self.app = app
        self.telemetry = telemetry

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        operation = _operation(
            str(scope.get("path", "")),
            str(scope.get("method", "GET")),
        )
        if operation is None:
            return await self.app(scope, receive, send)

        started = time.perf_counter()
        status_code = 500

        async def measured_send(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        try:
            return await self.app(scope, receive, measured_send)
        finally:
            outcome = (
                "success"
                if 200 <= status_code < 400
                else "client_error"
                if 400 <= status_code < 500
                else "server_error"
            )
            self.telemetry.observe_request(
                operation=operation,
                outcome=outcome,
                duration_ms=(time.perf_counter() - started) * 1000,
            )


__all__ = [
    "PlanningTelemetry",
    "PlanningTelemetryMiddleware",
    "PlanningTelemetrySnapshot",
]
