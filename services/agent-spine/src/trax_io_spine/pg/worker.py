"""Idle jobs worker (C2 spec §5): claims via FOR UPDATE SKIP LOCKED, dispatches
from HANDLERS (empty until C3), retries x3, dead-letters unknown kinds.
Run: python -m trax_io_spine.pg.worker  (env: WORKER_DATABASE_URL | DATABASE_URL,
WORKER_POLL_SECONDS default 5)."""
from __future__ import annotations

import logging
import os
import signal
import time
from collections.abc import Callable

from .db import make_pool

log = logging.getLogger("trax_io_spine.pg.worker")

HANDLERS: dict[str, Callable[[dict], None]] = {}
MAX_ATTEMPTS = 3

_CLAIM = """
update jobs set status = 'running', claimed_at = now(), attempts = attempts + 1
where id = (select id from jobs where status = 'queued'
            order by id limit 1 for update skip locked)
returning id, tenant_id::text, kind, payload, attempts
"""


def run_once(pool) -> bool:
    with pool.connection() as conn:
        row = conn.execute(_CLAIM).fetchone()
        if row is None:
            return False
        jid, _tenant, kind, payload, attempts = row
        handler = HANDLERS.get(kind)
        if handler is None:
            conn.execute(
                "update jobs set status = 'dead', finished_at = now(), error = %s "
                "where id = %s",
                (f"no handler registered for kind '{kind}'", jid),
            )
            return True
        try:
            handler(payload)
        except Exception as exc:  # noqa: BLE001 — the loop must survive any handler
            status = "failed" if attempts >= MAX_ATTEMPTS else "queued"
            conn.execute(
                "update jobs set status = %s, error = %s, "
                "finished_at = case when %s = 'failed' then now() end where id = %s",
                (status, f"{type(exc).__name__}: {exc}", status, jid),
            )
            return True
        conn.execute(
            "update jobs set status = 'done', finished_at = now(), error = null "
            "where id = %s",
            (jid,),
        )
        return True


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
