"""C3 Task 4: the self-serve upload ingest handler (spec §5-6).

`run_ingest` downloads a tenant's uploaded canonical files, parses + validates them
(Task 2/3's `trax_io_reco.ingest`), and — ONLY when validation is clean — runs the
recommendation engine over them and replaces the tenant's Postgres-seeded data via
`pg.seed.seed_store`. A dirty batch returns its `IngestError`s and MUST NOT touch any
existing seeded data (see tests/pg/test_c3_ingest_handler.py).

Seeding runs on `conn` — the connection the caller already opened to read the tenant's
`key_quota` — rather than on the `pool` argument. `pool` may be an RLS-restricted
app-role pool (e.g. the BFF's `trax_app`, which the writeback-history endpoints use);
only an elevated connection (the worker's, backed by the `trax_seed` role in
production, or a superuser in tests) is actually granted the cross-tenant writes
`seed_store` performs (upserting `tenants`, `part_keys`, `tenant_snapshots`, ...). So
`run_ingest` always seeds through `conn`, wrapped to satisfy `seed_store`'s
`pool.connection()` interface, and never depends on `pool`'s own privileges.

Review fix (C3 Task 4, CRITICAL): `seed_store`'s replace is DELETE-then-INSERT, not
concurrency-safe on its own. With >1 worker replica, the stale-`running` reclaim
(`worker.STALE_SECONDS`) can't tell a crashed worker from a legitimately-slow ingest,
so a second worker can start a fresh `run_ingest` for a tenant whose first run is
still in flight — two overlapping DELETE/INSERTs for the same tenant interleave and
leave doubled rows (`rec_id` is a random ULID, so no PK collision saves it). Before
seeding, `run_ingest` now takes a per-tenant `pg_advisory_xact_lock` on `conn` — the
SAME connection/transaction `seed_store` seeds on — so two overlapping runs for the
same tenant SERIALIZE: the second blocks until the first commits (releasing the
lock), then does its own clean delete+insert, leaving exactly one copy. See
`tests/pg/test_c3_ingest_handler.py`'s concurrency tests.

Review fix (C5 Task 10, CRITICAL): a scheduled recompute resolves WHICH payload to
replay on its own short-lived connection, before ever calling this function — so
resolving and seeding are two separate transactions, and a fresh upload-ingest for
the same tenant can COMMIT strictly in between. The advisory lock above serializes
the two seeds, but on its own does nothing to stop the recompute from then seeding
the OLDER payload it already resolved, silently reverting the newer upload. The
optional `guard` parameter closes that window: when given, it is invoked on THIS
connection immediately after the lock is acquired and before any seed write. Every
seed for a given tenant (`ingest` or `recompute`) takes this same lock first, so
nothing can commit new data for the tenant between the guard's check and this call's
own seed/commit — the check is therefore atomic with the seed it gates. `guard`
returning a non-`None` string means "abort, do not seed"; that string becomes the
`"superseded"` outcome's reason. Upload-ingest (`worker._ingest_handler`) never
passes one, so its behavior is byte-for-byte unchanged. See
`worker._superseded_reason` (the only caller that ever passes a `guard`) and
`tests/pg/test_c5_recompute_handler.py`.
"""
from __future__ import annotations

import dataclasses
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx
from trax_io_reco.ingest.mapper import to_extract_dir
from trax_io_reco.ingest.parse import parse_uploads
from trax_io_reco.ingest.validate import validate

from trax_io_spine.bff.store import PlannerStore
from trax_io_spine.pg.seed import seed_store


class StorageReader(Protocol):
    def download(self, path: str) -> bytes: ...


class IngestStorageError(RuntimeError):
    """Raised when a Storage download returns a non-2xx response."""


class HttpxStorageReader:
    """Downloads tenant-uploaded files from Supabase Storage via the service key."""

    def __init__(self, supabase_url: str, service_key: str, bucket: str = "tenant-uploads"):
        self._base = supabase_url.rstrip("/")
        self._key = service_key
        self._bucket = bucket

    def download(self, path: str) -> bytes:
        url = f"{self._base}/storage/v1/object/{self._bucket}/{path}"
        headers = {"Authorization": f"Bearer {self._key}", "apikey": self._key}
        resp = httpx.get(url, headers=headers, timeout=30)
        if resp.status_code // 100 != 2:
            raise IngestStorageError(
                f"download failed for {path!r}: {resp.status_code} {resp.text}"
            )
        return resp.content


class _ConnAsPool:
    """Adapts an already-open connection to the `pool.connection()` interface
    `seed_store` expects, so seeding runs on that connection instead of opening a
    fresh one off (possibly RLS-restricted) `pool`."""

    def __init__(self, conn):
        self._conn = conn

    @contextmanager
    def connection(self):
        yield self._conn


def _key_quota(conn, tenant_id: str) -> int | None:
    row = conn.execute(
        "select key_quota from tenants where id = %s::uuid", (tenant_id,)
    ).fetchone()
    return row[0] if row else None


def run_ingest(
    conn, pool, payload: dict, *, storage: StorageReader, tenant_name: str = "",
    preserve: frozenset[str] = frozenset(),
    guard: Callable[..., str | None] | None = None,
) -> dict:
    del pool  # seeding runs on `conn` — see module docstring
    tenant_id = payload["tenant_id"]
    tenant_slug = payload["tenant_slug"]
    key_quota = _key_quota(conn, tenant_id)

    # Each canonical file is downloaded as raw bytes; parse_uploads content-sniffs CSV vs
    # single-sheet .xlsx per file (minted Storage paths are extension-less, so a suffix
    # check can't tell them apart — see parse.py). The multi-sheet-workbook `xlsx=` shape
    # isn't reachable through the per-file mint route and is left to that parser's default.
    files: dict[str, bytes] = {
        name: storage.download(path) for name, path in payload["files"].items()
    }

    parsed = parse_uploads(files)
    errors = validate(parsed, key_quota=key_quota)
    if errors:
        return {"status": "failed", "errors": [dataclasses.asdict(e) for e in errors]}

    # Per-tenant advisory lock (see module docstring): transaction-scoped, so it is
    # released automatically when this call's transaction ends — either at
    # `seed_store`'s `conn.commit()` below, or (superseded path just below) at the
    # enclosing `pool.connection()` block's commit-on-clean-exit, since psycopg
    # wraps every checked-out connection in `with conn:` semantics. Taking it here
    # — on `conn`, before any seed work — means a second overlapping `run_ingest`
    # for the SAME tenant blocks until this one's transaction ends, instead of
    # racing its DELETE/INSERT and doubling rows.
    conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (tenant_id,))

    # C5 Task 10 review fix: re-check right here, on THIS connection, holding
    # THIS lock — see the module docstring for why this closes the reversion
    # window instead of just documenting it. `guard is None` (the upload path,
    # always) skips this block entirely, unchanged from before.
    if guard is not None:
        reason = guard(conn)
        if reason is not None:
            return {
                "status": "superseded",
                "result": {"outcome": "superseded", "reason": reason},
            }

    with tempfile.TemporaryDirectory() as tmp:
        to_extract_dir(parsed, Path(tmp), tenant_id=tenant_slug)
        store = PlannerStore.from_extract(
            tenant_id=tenant_slug, extract_dir=tmp, now=datetime.now(UTC)
        )
        report = seed_store(
            _ConnAsPool(conn), store=store, slug=tenant_slug, name=tenant_name,
            preserve=preserve,
        )

    return {
        "status": "done",
        "result": {
            "files": sorted(payload["files"]),
            # the raw ingested (pn, location) universe — not `report.part_keys`,
            # which is the (narrower) What-If-scorable subset requiring vendor
            # economics that a minimal upload (no `vendors` file) won't have.
            "keys": len(store.keys),
            "recommendations": report.recommendations,
            "seeded_at": datetime.now(UTC).isoformat(),
        },
    }
