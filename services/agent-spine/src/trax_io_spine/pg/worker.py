"""Idle jobs worker (C2 spec §5): claims via FOR UPDATE SKIP LOCKED, dispatches
from HANDLERS, retries x3, dead-letters unknown kinds.
Run: python -m trax_io_spine.pg.worker  (env: WORKER_DATABASE_URL | DATABASE_URL,
WORKER_POLL_SECONDS default 5).

Durability (C3 Task 4): claiming a job and running its handler are two SEPARATE
transactions — the claim (`status='running'`, `attempts += 1`) is committed on its
own BEFORE the handler is invoked, so a crash mid-handler leaves a durably 'running'
row rather than one whose claim silently rolled back with it. `_CLAIM` also reclaims
`running` jobs whose `claimed_at` is older than `STALE_SECONDS` (a worker that died
mid-handler) as long as `attempts < MAX_ATTEMPTS`, so a crashed run isn't lost forever.
The terminal status/result/error write is a third, separate transaction.

Review fix (C3 Task 4, CRITICAL): `STALE_SECONDS`-based reclaim alone can't tell a
crashed worker from a legitimately-slow ingest, so with >1 replica a second worker
could reclaim and re-run a still-in-flight `ingest` job — see `ingest.run_ingest`'s
per-tenant `pg_advisory_xact_lock`, which is the layer that actually makes an
overlapping reclaim harmless (it serializes the seed instead of double-executing
it). `STALE_SECONDS` itself was raised to 1800s so reclaim stays a rare backstop.

C5 Task 10: `HANDLERS["recompute"]` replays a tenant's data through the SAME
`run_ingest` path as an upload, but in preserve mode (the writeback ledger and
kill switch survive — see `pg.seed.seed_store`) and using the tenant's LATEST
completed `ingest` job's payload, resolved HERE at run time rather than
trusted from the recompute job's own `payload` column.
`enqueue_due_recomputes()` (migration 0014) deliberately stores no data
snapshot there — just `{"source": "recompute"}` — because its enqueue-time
dedup check is a non-atomic check-then-insert: a cron tick and a concurrent
user upload can both commit without either seeing the other, and a payload
captured at enqueue time could later replay a batch OLDER than one the user
just uploaded, silently reverting it. Since that marker payload carries no
tenant identity either, `_handler_payload` merges the claimed row's own
`tenant_id` in for `recompute` specifically — the only change to what
`run_once` hands a handler; `ingest` payloads already self-describe their own
(identical, by construction) `tenant_id` and pass through untouched.

Review fix (C5 Task 10, CRITICAL): resolving the payload above and seeding it
(inside `_run_job` -> `ingest.run_ingest`) are still two SEPARATE
transactions — `_recompute_handler` checks out its own connection to
resolve, `_run_job` checks out ANOTHER to seed. A user's upload-ingest for
the SAME tenant can therefore COMMIT strictly between them; the two seeds
still serialize on `run_ingest`'s per-tenant advisory lock, but without a
further check the recompute would go on to seed the OLDER payload it
already resolved, silently reverting that fresh upload once its turn comes.
`_last_done_ingest` now returns the resolved row's `id` alongside its
payload, and `run_ingest` gained an optional post-lock `guard` hook (see its
own module docstring) for exactly this. `_recompute_handler` passes a guard
(`_superseded_reason`) bound to the `tenant_id`/`id` it already resolved;
the guard re-runs a fresh query on the SAME connection `run_ingest` is about
to seed on, immediately after the lock and before any seed write. Because
every seed for a tenant takes that same lock first, nothing can commit new
data for the tenant between the guard's check and the seed that follows it
— the check's TIMING is atomic with the seed. The recompute then aborts
with a `"superseded"` outcome instead of seeding — `jobs.status` still
lands `'done'` (never `'failed'`: a superseded recompute is redundant, not
broken), with `result` recording plainly that it was skipped. See
`tests/pg/test_c5_recompute_handler.py`.

Review fix (C5 Task 10 review round 2 — final whole-branch review, Group C):
round 1's guard above compared only against `_last_done_ingest`'s
`status='done'` row. But `status='done'` is written in the THIRD, separate
transaction described at the top of this docstring (claim / handler /
terminal-write) — NOT the same transaction as the seed. With >1 worker
replica: replica B can seed and COMMIT a fresh upload — releasing the
advisory lock — before it ever writes `status='done'` for that job; the row
is still `'running'` for a beat. Replica A, unblocked the instant B releases
the lock, runs the guard immediately, does not see B's job as `'done'` yet,
and (round 1) would conclude "nothing newer" and go on to seed the STALE
payload it had already resolved — silently reverting B's just-committed
data. Round 1's atomicity property (check right after the lock, right
before the seed) was never the problem; the gap was in WHAT the check
looked for. `_superseded_reason` (see its own docstring) now looks for a
strictly newer `ingest` job in ANY of `'queued'`, `'running'`, or `'done'`
status — via `_newer_ingest_job_id` — not `'done'` alone, so a same-tenant
seed that is merely in flight also correctly supersedes this recompute.
`'failed'`/`'dead'` stay excluded: neither ever wrote data, so neither
supersedes anything. Skipping costs nothing either way: the other job's
seed lands anyway (this recompute was redundant) or fails validation (the
tenant simply keeps whatever this recompute would also have, redundantly,
written).
"""
from __future__ import annotations

import json
import logging
import math
import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from trax_io_reco.contracts.planning import (
    PortfolioSolveRequest,
    PortfolioSolveResult,
)
from trax_io_reco.contracts.replay import ReplayEvaluationRequest
from trax_io_reco.portfolio.optimizer import PortfolioOptimizer
from trax_io_reco.portfolio.run import enrich_planning_result_summary
from trax_io_reco.replay import build_shadow_scorecard

from trax_io_spine.operational_logging import log_operational_event

from .db import make_pool
from .ingest import HttpxStorageReader, run_ingest
from .planning import (
    load_planning_run_work,
    mark_planning_run_claimed,
    mark_planning_run_failed,
    mark_planning_run_retry,
    persist_planning_result,
)
from .replay import (
    load_replay_run_work,
    mark_replay_run_claimed,
    mark_replay_run_failed,
    mark_replay_run_retry,
    persist_replay_scorecard,
)

log = logging.getLogger("trax_io_spine.pg.worker")

# A handler may return `None` (legacy convention: always terminates the job 'done'
# with no `result`) or a dict shaped like `run_ingest`'s return value — `run_once`
# writes `jobs.result`/`jobs.error`/`status` from it accordingly.
HANDLERS: dict[str, Callable[[dict], dict | None]] = {}
MAX_ATTEMPTS = 3
# Crash-recovery backstop, not a liveness signal (C3 Task 4 review): a downloads +
# parse + engine-run + seed ingest can legitimately run well past a few minutes, and
# this value only needs to exceed the worst-case real run — it is what actually
# reclaims a job whose worker died mid-handler. It is NOT what prevents damage from
# a slow-but-alive run being "reclaimed" by a second replica: that's the per-tenant
# `pg_advisory_xact_lock` taken in `ingest.run_ingest`, which serializes concurrent
# seeds for the same tenant regardless of how long either one takes. 1800s (30min)
# is comfortably above any plausible ingest.
STALE_SECONDS = 1800


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    tenant_id: str
    kind: str
    payload: dict
    attempts: int


@dataclass(frozen=True)
class JobLifecycle:
    """Optional durable side effects committed with one job's state changes."""

    on_claim: Callable[[Any, ClaimedJob], None]
    on_attempt_failed: Callable[[Any, ClaimedJob, str, bool], None]
    on_terminal: Callable[[Any, ClaimedJob, dict | None], None]


LIFECYCLES: dict[str, JobLifecycle] = {}

_REPAIR_COVERAGE_COUNT_FIELDS = (
    "accepted",
    "excluded",
    "quarantined",
    "parts_covered",
    "shops_covered",
    "observed",
    "pooled",
    "proxy",
    "unavailable",
)
_REPAIR_PROXY_DEFINITION = "order_creation_to_last_receipt"
_INGEST_TELEMETRY_COUNT_FIELDS = (
    "open_order_po_count",
    "open_order_ro_count",
    "open_order_unknown_count",
    "open_order_legacy_fallback_count",
    "new_configured_fallback_count",
    "new_unavailable_count",
    "rep_configured_fallback_count",
    "rep_unavailable_count",
    "repair_duplicate_order_line_exclusion_count",
    "repair_duplicate_serial_exclusion_count",
)


def _bounded_repair_coverage(value: object) -> dict[str, int | str] | None:
    if not isinstance(value, dict):
        return None
    bounded: dict[str, int | str] = {}
    for field in _REPAIR_COVERAGE_COUNT_FIELDS:
        count = value.get(field)
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            return None
        bounded[field] = count
    if value.get("proxy_definition") == _REPAIR_PROXY_DEFINITION:
        bounded["proxy_definition"] = _REPAIR_PROXY_DEFINITION
    return bounded


def _bounded_ingest_telemetry(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        field: count
        for field in _INGEST_TELEMETRY_COUNT_FIELDS
        if (
            isinstance((count := value.get(field)), int)
            and not isinstance(count, bool)
            and count >= 0
        )
    }


def _failed_ingest_result(output: dict) -> dict[str, dict[str, object]]:
    errors = output.get("errors")
    summary: dict[str, object] = {
        "validation_error_count": len(errors) if isinstance(errors, list) else 0,
    }
    repair_coverage = _bounded_repair_coverage(output.get("repair_history"))
    if repair_coverage is not None:
        summary["repair_history"] = repair_coverage
    return {"validation_summary": summary}


def _log_ingest_terminal(
    job: ClaimedJob,
    output: dict | None,
    *,
    worker_duration_ms: float,
) -> None:
    failed = isinstance(output, dict) and output.get("status") == "failed"
    errors = output.get("errors") if isinstance(output, dict) else None
    result = output.get("result") if isinstance(output, dict) else None
    raw_repair = (
        output.get("repair_history")
        if failed and isinstance(output, dict)
        else result.get("repair_history")
        if isinstance(result, dict)
        else None
    )
    repair = _bounded_repair_coverage(raw_repair)
    telemetry = _bounded_ingest_telemetry(
        output.get("_telemetry") if isinstance(output, dict) else None
    )
    fields: dict[str, object] = {
        "event": "ingest_validation_terminal",
        "job_kind": job.kind,
        "status": "failed" if failed else "done",
        "worker_duration_ms": worker_duration_ms,
        "validation_error_count": (
            len(errors) if isinstance(errors, list) else 0
        ),
        "repair_accepted": repair.get("accepted") if repair else None,
        "repair_excluded": repair.get("excluded") if repair else None,
        "repair_quarantined": repair.get("quarantined") if repair else None,
        **{
            field: telemetry.get(field)
            for field in _INGEST_TELEMETRY_COUNT_FIELDS
        },
    }
    log_operational_event(
        log,
        logging.WARNING if failed else logging.INFO,
        "ingest_validation_terminal",
        **{
            key: value
            for key, value in fields.items()
            if key != "event"
        },
    )


def _planning_error_code(error: str, *, terminal: bool) -> str:
    if error.startswith("planning worker lease expired"):
        return "planning_worker_interrupted"
    return "planning_worker_failed" if terminal else "planning_worker_attempt_failed"


def _safe_job_error(
    job: ClaimedJob,
    error: str,
    *,
    terminal: bool,
    planning_code: str | None = None,
) -> str:
    """Redact durable planning/replay failures from the tenant-readable queue."""

    if job.kind not in {"planning", "replay"}:
        return error
    if job.kind == "replay":
        interrupted = error.startswith("replay worker lease expired")
        error_code = (
            "replay_worker_interrupted"
            if interrupted
            else (
                "replay_worker_failed"
                if terminal
                else "replay_worker_attempt_failed"
            )
        )
        return json.dumps(
            {
                "error_code": error_code,
                "retryable": not terminal,
            },
            sort_keys=True,
        )
    return json.dumps(
        {
            "error_code": planning_code
            or _planning_error_code(error, terminal=terminal),
            "retryable": not terminal,
        },
        sort_keys=True,
    )


def _planning_output_metrics(output: dict | None) -> dict[str, Any]:
    """Extract bounded operational dimensions without copying result payloads."""

    telemetry = output.get("_telemetry") if isinstance(output, dict) else None
    result = output.get("result") if isinstance(output, dict) else None

    def _field(container: object, name: str) -> Any:
        if isinstance(container, dict):
            return container.get(name)
        if isinstance(container, BaseModel):
            return getattr(container, name, None)
        return None

    solver = _field(result, "solver")

    def _safe_count(name: str) -> int | None:
        value = telemetry.get(name) if isinstance(telemetry, dict) else None
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else None
        )

    def _safe_nonnegative_number(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number >= 0 else None

    status = _field(result, "status")
    termination = _field(solver, "termination")
    return {
        "status": status if isinstance(status, str) else None,
        "key_count": _safe_count("key_count"),
        "candidate_count": _safe_count("candidate_count"),
        "solver_duration_ms": _safe_nonnegative_number(
            _field(solver, "duration_ms")
        ),
        "feasible": (
            status == "completed"
            and termination in {"optimal", "not_proven"}
        ),
        "termination": (
            termination if isinstance(termination, str) else None
        ),
        "optimality_gap": _safe_nonnegative_number(
            _field(solver, "relative_gap")
        ),
    }


def _log_planning_terminal(
    job: ClaimedJob,
    output: dict | None,
    *,
    worker_duration_ms: float,
) -> None:
    fields = {
        "attempt": job.attempts,
        "worker_duration_ms": worker_duration_ms,
        "reconciliation": "passed",
        **_planning_output_metrics(output),
    }
    level = logging.WARNING if fields["status"] == "failed" else logging.INFO
    log_operational_event(
        log,
        level,
        "planning_worker_terminal",
        **fields,
    )


def _log_planning_failure(
    job: ClaimedJob,
    output: dict | None,
    *,
    worker_duration_ms: float,
    failure_stage: str,
    error_type: str,
    terminal: bool,
) -> None:
    log_operational_event(
        log,
        logging.WARNING,
        "planning_worker_failure",
        attempt=job.attempts,
        worker_duration_ms=worker_duration_ms,
        reconciliation=(
            "failed" if failure_stage == "persistence" else "not_reached"
        ),
        failure_stage=failure_stage,
        error_type=error_type,
        terminal=terminal,
        **_planning_output_metrics(output),
    )


_CLAIM = """
update jobs set status = 'running', claimed_at = now(), attempts = attempts + 1
where id = (
    select id from jobs
    where status = 'queued'
       or (status = 'running' and attempts < %s
           and claimed_at < now() - (%s || ' seconds')::interval)
    order by id limit 1 for update skip locked
)
returning id, tenant_id::text, kind, payload, attempts
"""

_EXHAUSTED_DURABLE = """
select id, tenant_id::text, kind, payload, attempts
from jobs
where kind in ('planning', 'replay')
  and status = 'running'
  and attempts >= %s
  and claimed_at < now() - (%s || ' seconds')::interval
order by id
limit 1
for update skip locked
"""


def _handler_payload(kind: str, tenant_id: str, payload: dict) -> dict:
    """What a claimed job's handler actually receives (C5 Task 10).

    `recompute` rows carry no data snapshot in their `payload` column at all —
    by design (migration 0014's `enqueue_due_recomputes()`; see this module's
    docstring for why). The only place a claimed row's tenant identity lives
    is the `tenant_id` column this claim already reads, so it is merged in for
    `recompute` specifically. `ingest` payloads already self-describe their
    own (identical, by construction — `bff/ingest_routes.py`'s
    `create_ingest`) `tenant_id`, so this is scoped to `recompute` rather than
    applied unconditionally: `ingest`'s payload comes back as the exact same
    object, not a same-valued copy — upload-ingest stays byte-for-byte
    unchanged, not just value-unchanged.
    """
    if kind in {"recompute", "planning", "replay"}:
        return {**payload, "tenant_id": tenant_id}
    return payload


def _record_attempt_failure(
    pool,
    *,
    job: ClaimedJob,
    lifecycle: JobLifecycle | None,
    error: str,
) -> bool:
    terminal = job.attempts >= MAX_ATTEMPTS
    status = "failed" if terminal else "queued"
    stored_error = _safe_job_error(job, error, terminal=terminal)
    with pool.connection() as conn:
        if not _claim_is_current(conn, job):
            return False
        if lifecycle is not None:
            lifecycle.on_attempt_failed(conn, job, error, terminal)
        updated = conn.execute(
            "update jobs set status = %s, error = %s, "
            "finished_at = case when %s = 'failed' then now() end "
            "where id = %s and status = 'running' and attempts = %s "
            "returning id",
            (status, stored_error, status, job.id, job.attempts),
        ).fetchone()
        if updated is None:
            raise RuntimeError("job claim changed while recording attempt failure")
    return True


def _claim_is_current(conn, job: ClaimedJob) -> bool:
    """Lock and fence one terminal mutation to the exact claimed attempt."""

    return (
        conn.execute(
            """
            select 1
            from jobs
            where id = %s and status = 'running' and attempts = %s
            for update
            """,
            (job.id, job.attempts),
        ).fetchone()
        is not None
    )


def run_once(pool) -> bool:
    # Step 1: claim, committed in its OWN transaction before the handler runs — so
    # the 'running' status is durable (and visible to any other observer) even if
    # the handler below crashes the process outright. Planning's run transition is
    # a lifecycle hook in this SAME transaction, so job/run cannot disagree.
    with pool.connection() as conn:
        exhausted = conn.execute(
            _EXHAUSTED_DURABLE,
            (MAX_ATTEMPTS, STALE_SECONDS),
        ).fetchone()
        if exhausted is not None:
            job = ClaimedJob(
                id=exhausted[0],
                tenant_id=exhausted[1],
                kind=exhausted[2],
                payload=exhausted[3],
                attempts=exhausted[4],
            )
            error = (
                f"{job.kind} worker lease expired after the maximum number "
                f"of attempts ({job.attempts})"
            )
            lifecycle = LIFECYCLES.get(job.kind)
            if lifecycle is not None:
                lifecycle.on_attempt_failed(conn, job, error, True)
            conn.execute(
                "update jobs set status = 'failed', finished_at = now(), "
                "error = %s where id = %s",
                (_safe_job_error(job, error, terminal=True), job.id),
            )
            if job.kind == "planning":
                _log_planning_failure(
                    job,
                    None,
                    worker_duration_ms=0.0,
                    failure_stage="interruption",
                    error_type="WorkerLeaseExpired",
                    terminal=True,
                )
            return True
        row = conn.execute(_CLAIM, (MAX_ATTEMPTS, STALE_SECONDS)).fetchone()
        if row is None:
            return False
        job = ClaimedJob(
            id=row[0],
            tenant_id=row[1],
            kind=row[2],
            payload=row[3],
            attempts=row[4],
        )
        lifecycle = LIFECYCLES.get(job.kind)
        if lifecycle is not None:
            lifecycle.on_claim(conn, job)

    handler = HANDLERS.get(job.kind)
    if handler is None:
        error = f"no handler registered for kind '{job.kind}'"
        with pool.connection() as conn:
            if not _claim_is_current(conn, job):
                return True
            if lifecycle is not None:
                lifecycle.on_attempt_failed(conn, job, error, True)
            updated = conn.execute(
                "update jobs set status = 'dead', finished_at = now(), error = %s "
                "where id = %s and status = 'running' and attempts = %s "
                "returning id",
                (
                    _safe_job_error(job, error, terminal=True),
                    job.id,
                    job.attempts,
                ),
            ).fetchone()
            if updated is None:
                raise RuntimeError("job claim changed while dead-lettering")
        if job.kind == "planning":
            _log_planning_failure(
                job,
                None,
                worker_duration_ms=0.0,
                failure_stage="dispatch",
                error_type="MissingHandler",
                terminal=True,
            )
        return True

    # Step 2: run the handler on a fresh transaction/connection — not the one that
    # committed the claim, so a long-running handler doesn't hold that connection
    # idle-in-transaction.
    worker_started_at = time.perf_counter()
    try:
        result = handler(
            _handler_payload(job.kind, job.tenant_id, job.payload)
        )
    except Exception as exc:  # noqa: BLE001 — the loop must survive any handler
        _record_attempt_failure(
            pool,
            job=job,
            lifecycle=lifecycle,
            error=f"{type(exc).__name__}: {exc}",
        )
        if job.kind == "planning":
            _log_planning_failure(
                job,
                None,
                worker_duration_ms=(time.perf_counter() - worker_started_at) * 1000,
                failure_stage="handler",
                error_type=type(exc).__name__,
                terminal=job.attempts >= MAX_ATTEMPTS,
            )
        return True

    # Step 3: terminal update, its own transaction. A handler returning a
    # `{"status": "failed", "errors": [...]}` dict (a controlled, non-exception
    # failure — e.g. `run_ingest`'s validation errors) fails the job without
    # consuming the exception-based retry path; anything else (including `None`,
    # the legacy convention) marks it 'done', persisting `result` when present.
    try:
        with pool.connection() as conn:
            if not _claim_is_current(conn, job):
                return True
            if lifecycle is not None:
                lifecycle.on_terminal(conn, job, result)
            if isinstance(result, dict) and result.get("status") == "failed":
                error = json.dumps(result.get("errors", []))
                if job.kind == "planning":
                    error = _safe_job_error(
                        job,
                        error,
                        terminal=True,
                        planning_code="planning_solver_failed",
                    )
                failed_result = (
                    _failed_ingest_result(result)
                    if job.kind in {"ingest", "recompute"}
                    else None
                )
                updated = conn.execute(
                    "update jobs set status = 'failed', finished_at = now(), "
                    "error = %s, result = %s::jsonb "
                    "where id = %s and status = 'running' and attempts = %s "
                    "returning id",
                    (
                        error,
                        json.dumps(failed_result)
                        if failed_result is not None
                        else None,
                        job.id,
                        job.attempts,
                    ),
                ).fetchone()
            else:
                result_payload = (
                    json.dumps(result["result"])
                    if isinstance(result, dict) and "result" in result
                    else None
                )
                updated = conn.execute(
                    "update jobs set status = 'done', finished_at = now(), "
                    "error = null, result = %s "
                    "where id = %s and status = 'running' and attempts = %s "
                    "returning id",
                    (result_payload, job.id, job.attempts),
                ).fetchone()
            if updated is None:
                raise RuntimeError("job claim changed during terminal update")
    except Exception as exc:  # noqa: BLE001 — terminal persistence is retryable
        _record_attempt_failure(
            pool,
            job=job,
            lifecycle=lifecycle,
            error=f"{type(exc).__name__}: {exc}",
        )
        if job.kind == "planning":
            _log_planning_failure(
                job,
                result,
                worker_duration_ms=(time.perf_counter() - worker_started_at) * 1000,
                failure_stage="persistence",
                error_type=type(exc).__name__,
                terminal=job.attempts >= MAX_ATTEMPTS,
            )
        return True
    if job.kind == "planning":
        _log_planning_terminal(
            job,
            result,
            worker_duration_ms=(time.perf_counter() - worker_started_at) * 1000,
        )
    elif job.kind in {"ingest", "recompute"}:
        _log_ingest_terminal(
            job,
            result,
            worker_duration_ms=(time.perf_counter() - worker_started_at) * 1000,
        )
    return True


_ingest_pool = None  # lazily built — see `_get_ingest_pool`


def _get_ingest_pool():
    """Lazily-built shared pool for the `ingest`/`recompute` handlers, built
    from env rather than threaded through `HANDLERS`' `Callable[[dict], ...]`
    shape (unchanged so existing single-arg handlers/tests keep working)."""
    global _ingest_pool
    if _ingest_pool is None:
        url = os.environ.get("WORKER_DATABASE_URL") or os.environ["DATABASE_URL"]
        _ingest_pool = make_pool(url)
    return _ingest_pool


def _run_job(
    payload: dict, *, preserve: frozenset[str],
    guard: Callable[..., str | None] | None = None,
) -> dict:
    """Shared body for `ingest` and `recompute`: both replay a canonical batch
    from Storage through the engine and re-seed via `run_ingest`. They differ
    only in what the seed is allowed to delete (`preserve` — C5 spec §3.1,
    `pg.seed.seed_store`) and, `recompute`-only, an optional post-lock `guard`
    (C5 Task 10 review fix — see `ingest.run_ingest`'s docstring and
    `_superseded_reason`). `ingest` never passes one, so `guard` stays `None`
    all the way down to `run_ingest`'s own default — byte-for-byte the same
    call it always made."""
    pool = _get_ingest_pool()
    storage = HttpxStorageReader(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
    )
    with pool.connection() as conn:
        row = conn.execute(
            "select name from tenants where id = %s::uuid", (payload["tenant_id"],)
        ).fetchone()
        tenant_name = row[0] if row else ""
        return run_ingest(
            conn, pool, payload, storage=storage,
            tenant_name=tenant_name, preserve=preserve, guard=guard,
        )


def _ingest_handler(payload: dict) -> dict:
    """`HANDLERS["ingest"]` — downloads + validates + (re)seeds one tenant's
    upload via `run_ingest`, in full-replace mode (no `preserve`) — unchanged
    from before C5."""
    return _run_job(payload, preserve=frozenset())


_RECOMPUTE_PRESERVE = frozenset({"writeback_ledger", "kill_switches"})


def _last_done_ingest(conn, tenant_id: str) -> tuple[int, dict] | None:
    """The tenant's most recently COMPLETED upload-ingest job — its `(id,
    payload)` — resolved HERE, at run time, never trusted from the recompute
    job's OWN `payload` column (see `_recompute_handler`). Filters
    `kind = 'ingest'` specifically (not `in ('ingest', 'recompute')`) so a
    chain of prior recomputes is never replayed — only the tenant's actual
    last upload. `order by id desc` (not `created_at desc`): the identity PK
    is strictly monotonic with insertion order, so unlike a timestamp it
    cannot tie between two rows the same enqueue statement inserted in the
    same instant.

    The `id` (C5 Task 10 review fix) lets a caller compare it against what a
    LATER, guard-time check finds: see `_newer_ingest_job_id` /
    `_superseded_reason`, which run a broader query — any `'queued'`,
    `'running'`, or `'done'` job newer than this one (C5 Task 10 review
    round 2), not just another `'done'` one as round 1 checked — under
    `run_ingest`'s per-tenant advisory lock, to detect a same-tenant ingest
    that has already committed OR is still in flight since an earlier call
    to this function already resolved a payload to replay.
    """
    row = conn.execute(
        "select id, payload from jobs where tenant_id = %s::uuid and kind = 'ingest' "
        "and status = 'done' order by id desc limit 1",
        (tenant_id,),
    ).fetchone()
    return (row[0], row[1]) if row else None


def _newer_ingest_job_id(conn, tenant_id: str, after_job_id: int) -> tuple[int, str] | None:
    """The `(id, status)` of the tenant's most recent `ingest` job strictly
    newer than `after_job_id`, in any status that means its seed has already
    committed OR still might — `'queued'`, `'running'`, or `'done'`.
    `'failed'`/`'dead'` are excluded on purpose: neither ever wrote data, so
    neither supersedes anything a recompute is about to (redundantly) seed.

    Used only by `_superseded_reason` (C5 Task 10 review round 2) — a
    broader existence check than `_last_done_ingest`'s `'done'`-only query,
    which stays scoped to its own job of picking WHAT payload to replay, not
    whether to proceed.
    """
    row = conn.execute(
        "select id, status from jobs where tenant_id = %s::uuid and kind = 'ingest' "
        "and id > %s and status in ('queued', 'running', 'done') "
        "order by id desc limit 1",
        (tenant_id, after_job_id),
    ).fetchone()
    return (row[0], row[1]) if row else None


def _superseded_reason(conn, tenant_id: str, expected_job_id: int) -> str | None:
    """The `guard` `run_ingest` invokes for a `recompute` (C5 Task 10 review
    fix) — on the SAME connection/transaction, immediately after it acquires
    the per-tenant `pg_advisory_xact_lock` and before any seed write.

    Delegates to `_newer_ingest_job_id`: does a strictly newer `ingest` job
    exist for this tenant in `'queued'`, `'running'`, or `'done'` status (C5
    Task 10 review round 2 — see this module's top docstring for the
    residual race round 1's `'done'`-only check left open, and why
    `'failed'`/`'dead'` are excluded)? Postgres's default READ COMMITTED
    isolation means this fresh statement, run on THIS connection, sees every
    write committed up to this instant — and because EVERY seed for this
    tenant (`ingest` or `recompute`) takes this same advisory lock before
    writing anything, no other seed for this tenant can COMMIT between this
    check and the caller's own seed/commit that follows it. Both properties
    together — the broadened predicate AND the check's timing being atomic
    with the seed — are what make this guard complete: nothing that has
    already seeded, or is still in flight to seed, this tenant can slip past
    it unnoticed.

    Returns a human-readable reason when superseded, signalling "abort, do
    not seed"; `None` to proceed exactly as before. See
    `tests/pg/test_c5_recompute_handler.py`.
    """
    newer = _newer_ingest_job_id(conn, tenant_id, expected_job_id)
    if newer is None:
        return None
    newer_job_id, newer_status = newer
    return (
        f"tenant {tenant_id}: a newer ingest (job {newer_job_id}, status "
        f"'{newer_status}') landed after this recompute resolved job "
        f"{expected_job_id}; skipped to avoid overwriting it"
    )


def _recompute_handler(payload: dict) -> dict:
    """`HANDLERS["recompute"]` — C5's nightly scheduled recompute (spec §3.4).

    `enqueue_due_recomputes()` (migration 0014) deliberately enqueues a
    `recompute` job with NO data snapshot — just `{"source": "recompute"}` —
    because its own dedup check is a non-atomic check-then-insert: a cron
    tick and a concurrent user upload can both commit without seeing each
    other. A payload captured AT ENQUEUE time could freeze in a batch older
    than one the user finishes uploading moments later, and since the worker
    drains jobs in id order, blindly replaying that snapshot could silently
    revert the user's fresh data.

    So this handler resolves what to replay itself, right now: the tenant's
    latest COMPLETED (`status='done'`) `ingest` job's payload
    (`_last_done_ingest`). A race can then, at worst, replay data that
    is NEWER than what was known when this job was enqueued — never older.
    `tenant_id` arrives merged into `payload` by `run_once`/`_handler_payload`,
    since the enqueue-time marker payload itself carries none.

    Runs in preserve mode (`_RECOMPUTE_PRESERVE`): the append-only writeback
    ledger (rollback + SOC 2 audit evidence) and an operator's kill switch
    must never be silently reset by a scheduled job.

    C5 Task 10 review fix: the resolution above and the seed inside
    `_run_job` are still two separate transactions, so a fresh upload for
    this SAME tenant can commit strictly between them — this handler would
    otherwise go on to seed the now-stale payload it already resolved. A
    `guard` bound to exactly the `(tenant_id, job id)` just resolved is
    passed through `_run_job` to `run_ingest`, which invokes it on the SAME
    connection right after the advisory lock and before any seed write, so
    the check's timing is atomic with the seed — see `_superseded_reason`
    and `ingest.run_ingest`'s docstrings for the full guarantee (as of C5
    Task 10 review round 2, that also depends on the guard's predicate
    covering `'queued'`/`'running'` ingests, not just `'done'` ones). If it
    fires, `out` is the `"superseded"` dict `run_ingest` returns instead of
    seeding; this handler does not need to (and cannot, from out here) tell the difference — it
    just tags `source` on whatever `result` comes back, success or
    superseded alike, and returns it as-is. `run_once` treats anything other
    than `{"status": "failed", ...}` as `jobs.status = 'done'`, so a
    superseded outcome is correctly recorded as a successful, uneventful job
    — never a failure — with `result` naming plainly what happened.
    """
    tenant_id = payload["tenant_id"]
    pool = _get_ingest_pool()
    with pool.connection() as conn:
        resolved = _last_done_ingest(conn, tenant_id)

    if resolved is None:
        # Sane terminal outcome when there is nothing to replay: a tenant
        # whose ingest history was cleared, or a `recompute` job that somehow
        # got enqueued outside `enqueue_due_recomputes()`'s own "has a prior
        # done ingest" eligibility gate. Fail the JOB cleanly and legibly —
        # not an exception (retrying changes nothing about this outcome), and
        # never a silent no-op that reports success without replaying
        # anything.
        return {
            "status": "failed",
            "errors": [
                f"no completed ingest found for tenant {tenant_id}; nothing to recompute"
            ],
        }
    ingest_job_id, ingest_payload = resolved

    def _guard(seed_conn) -> str | None:
        return _superseded_reason(seed_conn, tenant_id, ingest_job_id)

    out = _run_job(ingest_payload, preserve=_RECOMPUTE_PRESERVE, guard=_guard)
    if isinstance(out, dict) and isinstance(out.get("result"), dict):
        out["result"]["source"] = "recompute"
    return out


def _planning_handler(payload: dict) -> dict:
    """Solve only the immutable request captured by the submitted run."""

    pool = _get_ingest_pool()
    with pool.connection() as conn:
        work = load_planning_run_work(
            conn,
            tenant_uuid=payload["tenant_id"],
            run_id=payload["run_id"],
        )

    request = work.request
    solved = PortfolioOptimizer().solve(request)
    solved = enrich_planning_result_summary(
        request=request,
        result=solved,
    )
    return {
        "status": solved.status,
        # Internal worker handoff: keep frozen typed graphs by reference until
        # the terminal transaction validates and writes each normalized row.
        # The lifecycle hook replaces these with a bounded JSON header before
        # ``jobs.result`` or telemetry sees the output.
        "result": solved,
        "_request": request,
        "_telemetry": {
            "candidate_count": sum(
                len(menu.frontier.candidates) for menu in request.menus
            ),
            "key_count": len(request.menus),
        },
        "detail": {
            "contract_version": "planning-run.v1",
            "parent_run_id": work.parent_run_id,
            "parent_planning_fingerprint": work.parent_planning_fingerprint,
            "parent_source_snapshot_hash": work.parent_source_snapshot_hash,
            "_derive_selection_details": solved.status == "completed",
            "assumption_diff": list(work.assumption_diff),
            "warnings": [],
        },
        "errors": (
            [solved.solver.message]
            if solved.status == "failed"
            else []
        ),
    }


def _planning_on_claim(conn, job: ClaimedJob) -> None:
    mark_planning_run_claimed(
        conn,
        tenant_uuid=job.tenant_id,
        run_id=job.payload["run_id"],
        attempts=job.attempts,
    )


def _planning_on_attempt_failed(
    conn,
    job: ClaimedJob,
    error: str,
    terminal: bool,
) -> None:
    marker = mark_planning_run_failed if terminal else mark_planning_run_retry
    marker(
        conn,
        tenant_uuid=job.tenant_id,
        run_id=job.payload["run_id"],
        attempts=job.attempts,
        error=error,
    )


def _planning_on_terminal(
    conn,
    job: ClaimedJob,
    output: dict | None,
) -> None:
    if not isinstance(output, dict):
        raise ValueError("planning handler must return a result envelope")
    result = output.get("result")
    if not isinstance(result, (dict, PortfolioSolveResult)):
        raise ValueError("planning handler result envelope is missing result")
    detail = output.get("detail", {})
    if not isinstance(detail, dict):
        raise ValueError("planning handler detail must be a JSON object")
    trusted_request = output.pop("_request", None)
    if trusted_request is not None and not isinstance(
        trusted_request,
        PortfolioSolveRequest,
    ):
        raise ValueError("planning handler request envelope is invalid")
    bounded_result = persist_planning_result(
        conn,
        tenant_uuid=job.tenant_id,
        run_id=job.payload["run_id"],
        attempts=job.attempts,
        result=result,
        detail=detail,
        trusted_request=trusted_request,
    )
    output["result"] = bounded_result
    # Drop the 59K normalized detail graph before the job header update/log.
    output["detail"] = {}


def _replay_handler(payload: dict) -> dict:
    """Build one advisory scorecard from the immutable stored replay manifest."""

    pool = _get_ingest_pool()
    with pool.connection() as conn:
        work = load_replay_run_work(
            conn,
            tenant_uuid=payload["tenant_id"],
            replay_id=payload["replay_id"],
        )
    request = ReplayEvaluationRequest.model_validate(work.request)
    scorecard = build_shadow_scorecard(request)
    return {
        "status": "completed",
        "result": scorecard.model_dump(mode="json"),
    }


def _replay_on_claim(conn, job: ClaimedJob) -> None:
    mark_replay_run_claimed(
        conn,
        tenant_uuid=job.tenant_id,
        replay_id=job.payload["replay_id"],
        attempts=job.attempts,
    )


def _replay_on_attempt_failed(
    conn,
    job: ClaimedJob,
    error: str,
    terminal: bool,
) -> None:
    marker = mark_replay_run_failed if terminal else mark_replay_run_retry
    marker(
        conn,
        tenant_uuid=job.tenant_id,
        replay_id=job.payload["replay_id"],
        attempts=job.attempts,
        error=error,
    )


def _replay_on_terminal(
    conn,
    job: ClaimedJob,
    output: dict | None,
) -> None:
    if not isinstance(output, dict) or output.get("status") != "completed":
        raise ValueError("replay handler must return a completed result envelope")
    result = output.get("result")
    if not isinstance(result, dict):
        raise ValueError("replay handler result envelope is missing scorecard")
    output["result"] = persist_replay_scorecard(
        conn,
        tenant_uuid=job.tenant_id,
        replay_id=job.payload["replay_id"],
        attempts=job.attempts,
        scorecard=result,
    )


HANDLERS["ingest"] = _ingest_handler
HANDLERS["recompute"] = _recompute_handler
HANDLERS["planning"] = _planning_handler
HANDLERS["replay"] = _replay_handler
LIFECYCLES["planning"] = JobLifecycle(
    on_claim=_planning_on_claim,
    on_attempt_failed=_planning_on_attempt_failed,
    on_terminal=_planning_on_terminal,
)
LIFECYCLES["replay"] = JobLifecycle(
    on_claim=_replay_on_claim,
    on_attempt_failed=_replay_on_attempt_failed,
    on_terminal=_replay_on_terminal,
)


def run_forever(database_url: str, poll_seconds: float) -> None:
    pool = make_pool(database_url)
    stop = {"flag": False}

    def _sig(*_a):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    log.info("worker up (poll=%ss, handlers=%s)", poll_seconds, sorted(HANDLERS))
    while not stop["flag"]:
        worked = run_once(pool)
        if not worked:
            time.sleep(poll_seconds)
    log.info("worker shutting down")
    pool.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    url = os.environ.get("WORKER_DATABASE_URL") or os.environ["DATABASE_URL"]
    run_forever(url, float(os.environ.get("WORKER_POLL_SECONDS", "5")))


if __name__ == "__main__":
    main()
