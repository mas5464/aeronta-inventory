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
"""
from __future__ import annotations

import json
import logging
import os
import signal
import time
from collections.abc import Callable

from .db import make_pool
from .ingest import HttpxStorageReader, run_ingest

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


def run_once(pool) -> bool:
    # Step 1: claim, committed in its OWN transaction before the handler runs — so
    # the 'running' status is durable (and visible to any other observer) even if
    # the handler below crashes the process outright.
    with pool.connection() as conn:
        row = conn.execute(_CLAIM, (MAX_ATTEMPTS, STALE_SECONDS)).fetchone()
    if row is None:
        return False
    jid, _tenant, kind, payload, attempts = row

    handler = HANDLERS.get(kind)
    if handler is None:
        with pool.connection() as conn:
            conn.execute(
                "update jobs set status = 'dead', finished_at = now(), error = %s "
                "where id = %s",
                (f"no handler registered for kind '{kind}'", jid),
            )
        return True

    # Step 2: run the handler on a fresh transaction/connection — not the one that
    # committed the claim, so a long-running handler doesn't hold that connection
    # idle-in-transaction.
    try:
        result = handler(payload)
    except Exception as exc:  # noqa: BLE001 — the loop must survive any handler
        status = "failed" if attempts >= MAX_ATTEMPTS else "queued"
        with pool.connection() as conn:
            conn.execute(
                "update jobs set status = %s, error = %s, "
                "finished_at = case when %s = 'failed' then now() end where id = %s",
                (status, f"{type(exc).__name__}: {exc}", status, jid),
            )
        return True

    # Step 3: terminal update, its own transaction. A handler returning a
    # `{"status": "failed", "errors": [...]}` dict (a controlled, non-exception
    # failure — e.g. `run_ingest`'s validation errors) fails the job without
    # consuming the exception-based retry path; anything else (including `None`,
    # the legacy convention) marks it 'done', persisting `result` when present.
    with pool.connection() as conn:
        if isinstance(result, dict) and result.get("status") == "failed":
            conn.execute(
                "update jobs set status = 'failed', finished_at = now(), "
                "error = %s, result = null where id = %s",
                (json.dumps(result.get("errors", [])), jid),
            )
        else:
            result_payload = (
                json.dumps(result["result"])
                if isinstance(result, dict) and "result" in result
                else None
            )
            conn.execute(
                "update jobs set status = 'done', finished_at = now(), "
                "error = null, result = %s where id = %s",
                (result_payload, jid),
            )
    return True


_ingest_pool = None  # lazily built — see `_ingest_handler`


def _ingest_handler(payload: dict) -> dict:
    """`HANDLERS["ingest"]` — downloads + validates + (re)seeds one tenant's upload
    via `run_ingest`. Builds its own pool/StorageReader from env rather than
    threading them through `HANDLERS`' `Callable[[dict], ...]` shape (unchanged so
    existing single-arg handlers/tests keep working)."""
    global _ingest_pool
    if _ingest_pool is None:
        url = os.environ.get("WORKER_DATABASE_URL") or os.environ["DATABASE_URL"]
        _ingest_pool = make_pool(url)
    storage = HttpxStorageReader(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
    )
    with _ingest_pool.connection() as conn:
        row = conn.execute(
            "select name from tenants where id = %s::uuid", (payload["tenant_id"],)
        ).fetchone()
        tenant_name = row[0] if row else ""
        return run_ingest(
            conn, _ingest_pool, payload, storage=storage, tenant_name=tenant_name
        )


HANDLERS["ingest"] = _ingest_handler


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
