"""C5 Task 10: `HANDLERS["recompute"]` — replays a tenant's data in preserve
mode (writeback ledger + kill switch survive) so a nightly scheduled recompute
never destroys audit evidence or a safety control.

IMPORTANT DEVIATION FROM THE ORIGINAL TASK BRIEF: the brief's own test/snippet
passed the recompute job's STORED `payload` straight into `run_ingest`. That
is wrong and is deliberately NOT reproduced here. `enqueue_due_recomputes()`
(migration `20260724000014_enqueue_due_recomputes.sql`) was adjudicated to
enqueue a `recompute` job with NO data snapshot at all — just
`{"source": "recompute"}` — precisely BECAUSE its enqueue-time dedup check is
a non-atomic check-then-insert: a nightly cron tick and a concurrent user
upload can both commit without either seeing the other. A payload captured at
ENQUEUE time could freeze in a batch older than one the user finishes
uploading moments later; since the worker drains `jobs` in id order, blindly
replaying that stale snapshot could silently REVERT the user's fresh upload.

So `_recompute_handler` resolves what to replay itself, at RUN time: the
tenant's most recent `status='done'` `ingest` job's payload (see
`_last_done_ingest`, renamed from `_last_done_ingest_payload` in the fix
round below). A race can then, at worst, replay data that is NEWER than
what was known at enqueue time — never older.

`test_recompute_replays_the_newest_completed_ingest_not_the_job_payload` is
the regression guard for exactly this: it must fail if this handler (or its
resolution query) ever goes back to trusting `job["payload"]` directly.

Fix round 1 (residual data-reversion window): resolving the payload above and
seeding it are still two SEPARATE transactions (`_recompute_handler` checks
out one connection to resolve, `_run_job` checks out another to seed) — so a
user's upload-ingest for the SAME tenant can commit strictly between them,
and this handler would otherwise silently seed the now-stale payload it
already resolved, overwriting that fresh upload once its seed serializes in
behind the shared advisory lock. `_last_done_ingest` (renamed from
`_last_done_ingest_payload`) now returns the resolved job's `id` too, and
`run_ingest` gained an optional post-lock `guard` hook that
`_recompute_handler` uses (`_superseded_reason`) to re-check, under the
SAME lock, whether a newer ingest landed since. The tests below split into:
guard-only unit tests (`test_superseded_reason_*`, no `run_ingest`
involved), one test that drives `worker._run_job` directly to prove the
real, locked seed is actually skipped (not just that a mock was told to
skip it), and one that drives the full `worker.HANDLERS["recompute"]` entry
point to prove the ordinary (non-superseded) path still seeds for real.

Fix round 2 (final whole-branch review, Group C — residual gap in round 1's
own predicate): round 1's guard compared only against a newer `ingest` job
that had already reached `status='done'`. But `'done'` is written in a
THIRD, separate transaction from the seed itself (`run_once`'s
claim/handler/terminal-write split — see worker.py's module docstring), so
with >1 worker replica a newer ingest's seed can already have committed —
releasing the advisory lock — while its own row is still `'running'` for a
beat. A concurrent recompute's round-1 guard, unblocked the instant that
lock releases, would not see the newer job as `'done'` yet and would
wrongly proceed to seed a stale payload over it. `_superseded_reason` now
checks for a strictly newer `ingest` job in ANY of `'queued'`, `'running'`,
or `'done'` (via `_newer_ingest_job_id`) — not `'done'` alone — while still
correctly ignoring `'failed'`/`'dead'` ones, which never wrote data.
`test_superseded_reason_detects_a_newer_ingest_still_running_not_yet_done`
and `test_superseded_reason_detects_a_newer_ingest_still_queued` are the
regression guards for this exact window;
`test_superseded_reason_ignores_a_newer_ingest_that_failed_or_died` guards
the deliberate exclusion.
"""
from __future__ import annotations

import json

from trax_io_spine.pg import ingest as ingest_mod
from trax_io_spine.pg import worker

# --- canonical fixtures for the real (non-mocked) `run_ingest` tests below --
# (mirrors tests/pg/test_c3_ingest_handler.py's PARTS/STOCK/DEMAND/FakeStorage,
# kept local so this file stays self-contained.) "_1" seeds a single key
# (P1); "_2" seeds two (P1, P2) — the count difference is what the
# superseded-vs-normal tests below assert on to prove data was (or wasn't)
# reverted, rather than just inspecting `run_ingest`'s return value.

_PARTS_1 = b"part_number,part_class,unit_cost,criticality\nP1,rotable,100,AOG\n"
_STOCK_1 = (
    b"part_number,location_code,on_hand,current_rop,current_eoq,"
    b"current_safety_stock,current_max\nP1,MIA,5,3,10,2,20\n"
)
_DEMAND_1 = b"part_number,location_code,period,quantity\nP1,MIA,2026-01-01,3\n"

_PARTS_2 = (
    b"part_number,part_class,unit_cost,criticality\n"
    b"P1,rotable,100,AOG\nP2,rotable,50,routine\n"
)
_STOCK_2 = (
    b"part_number,location_code,on_hand,current_rop,current_eoq,"
    b"current_safety_stock,current_max\n"
    b"P1,MIA,5,3,10,2,20\nP2,MIA,8,4,12,3,25\n"
)
_DEMAND_2 = (
    b"part_number,location_code,period,quantity\n"
    b"P1,MIA,2026-01-01,3\nP2,MIA,2026-01-01,2\n"
)
# A `vendors` file is required for a key to land in `part_keys`/`part_contexts`
# at all (`bff/scenario.py`'s `build_key_stats` calls `fs.get_vendor_economics`
# and silently `continue`s past any key that raises — confirmed empirically:
# without this, `store.keys`/`out["result"]["keys"]` is still correct, but
# `part_keys` ends up with ZERO rows regardless of how many keys were
# ingested, which would make `_key_count` useless as a superseded-vs-normal
# signal below).
_VENDORS_1 = b"part_number,vendor_code,unit_price,lead_time_days\nP1,V1,100,14\n"
_VENDORS_2 = (
    b"part_number,vendor_code,unit_price,lead_time_days\n"
    b"P1,V1,100,14\nP2,V1,50,10\n"
)


class _FakeStorage:
    def __init__(self, blobs: dict[str, bytes]):
        self._blobs = blobs

    def download(self, path: str) -> bytes:
        return self._blobs[path]


def _key_count(conn, tenant_id: str) -> int:
    return conn.execute(
        "select count(*) from part_keys where tenant_id = %s::uuid", (tenant_id,)
    ).fetchone()[0]

# --- fixtures (raw SQL — mirrors test_c5_enqueue_recomputes.py / test_worker.py) --


def _tenant(conn, slug: str) -> str:
    row = conn.execute(
        "insert into tenants (slug, name) values (%s, %s) "
        "on conflict (slug) do update set name = excluded.name returning id::text",
        (slug, slug),
    ).fetchone()
    conn.commit()
    return row[0]


def _done_ingest(conn, tenant_id: str, payload: dict, status: str = "done") -> int:
    """Inserts an `ingest` job row, `'done'` by default. `status` is
    overridable (C5 Task 10 review round 2's `'queued'`/`'running'` race
    tests below need a job that exists but has NOT yet reached `'done'` —
    see `test_superseded_reason_detects_a_newer_ingest_still_in_flight`)."""
    row = conn.execute(
        "insert into jobs (tenant_id, kind, payload, status) "
        "values (%s::uuid, 'ingest', %s::jsonb, %s) returning id",
        (tenant_id, json.dumps(payload), status),
    ).fetchone()
    conn.commit()
    return row[0]


def _done_recompute(conn, tenant_id: str, payload: dict) -> None:
    conn.execute(
        "insert into jobs (tenant_id, kind, payload, status) "
        "values (%s::uuid, 'recompute', %s::jsonb, 'done')",
        (tenant_id, json.dumps(payload)),
    )
    conn.commit()


# --- registration -----------------------------------------------------------


def test_recompute_handler_is_registered():
    assert "recompute" in worker.HANDLERS


# --- run_once -> handler payload contract -----------------------------------


def test_handler_payload_merges_tenant_id_for_recompute_only():
    """`run_once`'s dispatch contract (`_handler_payload`): a claimed
    `recompute` row's `payload` column carries no tenant identity (migration
    0014), so the tenant_id the claim already read off the row is merged in.
    An `ingest` payload already self-describes its own (identical, by
    construction — `bff/ingest_routes.py`'s `create_ingest`) `tenant_id`, and
    must come back completely untouched — the SAME object, not a copy — so
    upload-ingest stays byte-for-byte unchanged."""
    recompute_payload = {"source": "recompute"}
    merged = worker._handler_payload("recompute", "T1", recompute_payload)
    assert merged == {"source": "recompute", "tenant_id": "T1"}

    ingest_payload = {"tenant_id": "T1", "files": {"parts": "p"}}
    passthrough = worker._handler_payload("ingest", "T1", ingest_payload)
    assert passthrough is ingest_payload


# --- the regression guard ----------------------------------------------------


def test_recompute_replays_the_newest_completed_ingest_not_the_job_payload(
    admin_pool, monkeypatch
):
    with admin_pool.connection() as conn:
        tenant_id = _tenant(conn, "c5-recompute-newest")
        _done_ingest(conn, tenant_id, {
            "tenant_id": tenant_id, "tenant_slug": "c5-recompute-newest",
            "batch_id": "OLD", "files": {"parts": "old/parts"}, "uploaded_by": "u1",
        })
        _done_ingest(conn, tenant_id, {
            "tenant_id": tenant_id, "tenant_slug": "c5-recompute-newest",
            "batch_id": "NEW", "files": {"parts": "new/parts"}, "uploaded_by": "u1",
        })

    seen = {}

    def _fake_run_ingest(
        conn, pool, payload, *, storage, tenant_name="", preserve=frozenset(), guard=None
    ):
        seen["payload"] = payload
        seen["preserve"] = preserve
        # `guard` (C5 Task 10 review fix) must arrive as a real, callable
        # closure — not merely accepted and ignored. Invoking it here, for
        # real, against the real rows seeded above (nothing has changed
        # since `_recompute_handler` resolved "NEW" a moment ago) proves the
        # ordinary case's guard genuinely does not fire.
        if guard is not None:
            seen["guard_result"] = guard(conn)
        return {"status": "done", "result": {"keys": 3}}

    monkeypatch.setattr(worker, "run_ingest", _fake_run_ingest)
    monkeypatch.setattr(worker, "_ingest_pool", admin_pool, raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")

    # This is exactly the shape `run_once`/`_handler_payload` hands the
    # handler for a real claimed 'recompute' row: the enqueue-time marker
    # (migration 0014) plus the merged tenant_id — never a data snapshot.
    out = worker.HANDLERS["recompute"]({"source": "recompute", "tenant_id": tenant_id})

    assert seen["payload"]["batch_id"] == "NEW", (
        "must replay the NEWEST completed ingest, never the job's own stored payload"
    )
    assert seen["payload"]["files"] == {"parts": "new/parts"}
    assert seen["preserve"] == frozenset({"writeback_ledger", "kill_switches"})
    assert seen["guard_result"] is None, "nothing raced, so the guard must not fire"
    assert out["result"]["source"] == "recompute"
    assert out["result"]["keys"] == 3


def test_recompute_uses_the_upload_ingest_not_a_prior_recompute(admin_pool, monkeypatch):
    """A previously-run `recompute` job is itself `kind='recompute'` — never
    `kind='ingest'`. `_last_done_ingest` must keep resolving the
    tenant's actual last UPLOAD, not chain off a prior recompute's own
    (irrelevant) `{"source": "recompute"}` marker payload, even though that
    row is more recent."""
    with admin_pool.connection() as conn:
        tenant_id = _tenant(conn, "c5-recompute-chain")
        _done_ingest(conn, tenant_id, {
            "tenant_id": tenant_id, "tenant_slug": "c5-recompute-chain",
            "batch_id": "UPLOAD", "files": {"parts": "upload/parts"}, "uploaded_by": "u1",
        })
        _done_recompute(conn, tenant_id, {"source": "recompute"})  # more recent, but not an upload

    seen = {}

    def _fake_run_ingest(
        conn, pool, payload, *, storage, tenant_name="", preserve=frozenset(), guard=None
    ):
        seen["payload"] = payload
        return {"status": "done", "result": {"keys": 1}}

    monkeypatch.setattr(worker, "run_ingest", _fake_run_ingest)
    monkeypatch.setattr(worker, "_ingest_pool", admin_pool, raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")

    worker.HANDLERS["recompute"]({"source": "recompute", "tenant_id": tenant_id})

    assert seen["payload"]["batch_id"] == "UPLOAD"


# --- the missing-history terminal outcome -----------------------------------


def test_recompute_fails_cleanly_with_no_completed_ingest(admin_pool, monkeypatch):
    """A recompute enqueued for a tenant with NO completed ingest to replay
    (history cleared, or a job that somehow got enqueued bypassing
    `enqueue_due_recomputes()`'s own "has a prior done ingest" eligibility
    gate) must fail the JOB cleanly with a legible error — never crash, and
    never silently report success without replaying anything."""
    with admin_pool.connection() as conn:
        tenant_id = _tenant(conn, "c5-recompute-empty")

    monkeypatch.setattr(worker, "_ingest_pool", admin_pool, raising=False)

    out = worker.HANDLERS["recompute"]({"source": "recompute", "tenant_id": tenant_id})

    assert out["status"] == "failed"
    assert out["errors"]
    assert tenant_id in out["errors"][0]


# --- Fix round 1: the guard itself, tested directly (no run_ingest) ---------


def test_superseded_reason_is_none_when_nothing_newer_landed(admin_pool):
    """The guard, called directly: no ingest newer than `expected_job_id`
    exists yet, so it must proceed (`None`) — exactly the pre-race state."""
    with admin_pool.connection() as conn:
        tenant_id = _tenant(conn, "c5-guard-clean")
        job_id = _done_ingest(conn, tenant_id, {
            "tenant_id": tenant_id, "tenant_slug": "c5-guard-clean",
            "batch_id": "ONLY", "files": {"parts": "only/parts"}, "uploaded_by": "u1",
        })

    with admin_pool.connection() as conn:
        assert worker._superseded_reason(conn, tenant_id, job_id) is None


def test_superseded_reason_detects_a_newer_ingest_committed_after_resolution(admin_pool):
    """The core detection logic in isolation: this is the EXACT check
    `run_ingest` runs under the advisory lock. Must fail if the query ever
    regresses to ignoring the newer row or comparing the wrong column."""
    with admin_pool.connection() as conn:
        tenant_id = _tenant(conn, "c5-guard-race")
        old_job_id = _done_ingest(conn, tenant_id, {
            "tenant_id": tenant_id, "tenant_slug": "c5-guard-race",
            "batch_id": "OLD", "files": {"parts": "old/parts"}, "uploaded_by": "u1",
        })

    with admin_pool.connection() as conn:
        assert worker._superseded_reason(conn, tenant_id, old_job_id) is None

    # The race: a fresh upload-ingest job COMMITS.
    with admin_pool.connection() as conn:
        new_job_id = _done_ingest(conn, tenant_id, {
            "tenant_id": tenant_id, "tenant_slug": "c5-guard-race",
            "batch_id": "NEW", "files": {"parts": "new/parts"}, "uploaded_by": "u1",
        })

    with admin_pool.connection() as conn:
        reason = worker._superseded_reason(conn, tenant_id, old_job_id)

    assert reason is not None
    assert str(old_job_id) in reason
    assert str(new_job_id) in reason
    assert tenant_id in reason


# --- Fix round 2: the residual race — newer job not yet 'done' --------------


def test_superseded_reason_detects_a_newer_ingest_still_running_not_yet_done(admin_pool):
    """C5 Task 10 review round 2 (final whole-branch review, Group C): the
    residual race round 1 left open. `run_once` writes `status='done'` in a
    THIRD, separate transaction from the seed itself (see worker.py's own
    module docstring on claim/handler/terminal-write) — so with >1 worker
    replica, a newer ingest job can have ALREADY SEEDED (and released the
    per-tenant advisory lock) while its own row is still `'running'` for a
    beat, its terminal write not yet landed. A round-1 guard — comparing
    only against `_last_done_ingest`'s `'done'`-only query — would not see
    this job as newer yet and would wrongly report "nothing newer,"
    proceeding to seed a stale payload over the fresh data. This is that
    exact window, with no `'done'` row involved at all: must fail if the
    predicate ever regresses to checking `'done'` only."""
    with admin_pool.connection() as conn:
        tenant_id = _tenant(conn, "c5-guard-race-running")
        old_job_id = _done_ingest(conn, tenant_id, {
            "tenant_id": tenant_id, "tenant_slug": "c5-guard-race-running",
            "batch_id": "OLD", "files": {"parts": "old/parts"}, "uploaded_by": "u1",
        })

    with admin_pool.connection() as conn:
        assert worker._superseded_reason(conn, tenant_id, old_job_id) is None

    # The race: a fresh upload-ingest job commits its claim — exactly what
    # `_CLAIM` writes and commits BEFORE the handler (and its eventual seed
    # + terminal 'done' write, a separate later transaction) ever runs. No
    # 'done' row exists for it yet, only 'running'.
    with admin_pool.connection() as conn:
        new_job_id = _done_ingest(
            conn, tenant_id, {
                "tenant_id": tenant_id, "tenant_slug": "c5-guard-race-running",
                "batch_id": "NEW", "files": {"parts": "new/parts"}, "uploaded_by": "u1",
            },
            status="running",
        )

    with admin_pool.connection() as conn:
        reason = worker._superseded_reason(conn, tenant_id, old_job_id)

    assert reason is not None, (
        "a newer ingest that is merely 'running' (not yet 'done') must still "
        "supersede — its seed may already have committed"
    )
    assert str(old_job_id) in reason
    assert str(new_job_id) in reason
    assert "running" in reason


def test_superseded_reason_detects_a_newer_ingest_still_queued(admin_pool):
    """Same predicate, the other non-`'done'` end of the spectrum: a newer
    ingest that has not even been claimed yet (`'queued'`) still supersedes.
    Skipping costs nothing — that job's seed lands on its own once it runs,
    or it fails validation and the tenant simply keeps whatever this
    recompute would also have (redundantly) written."""
    with admin_pool.connection() as conn:
        tenant_id = _tenant(conn, "c5-guard-race-queued")
        old_job_id = _done_ingest(conn, tenant_id, {
            "tenant_id": tenant_id, "tenant_slug": "c5-guard-race-queued",
            "batch_id": "OLD", "files": {"parts": "old/parts"}, "uploaded_by": "u1",
        })
        new_job_id = _done_ingest(
            conn, tenant_id, {
                "tenant_id": tenant_id, "tenant_slug": "c5-guard-race-queued",
                "batch_id": "NEW", "files": {"parts": "new/parts"}, "uploaded_by": "u1",
            },
            status="queued",
        )

    with admin_pool.connection() as conn:
        reason = worker._superseded_reason(conn, tenant_id, old_job_id)

    assert reason is not None
    assert str(new_job_id) in reason
    assert "queued" in reason


def test_superseded_reason_ignores_a_newer_ingest_that_failed_or_died(admin_pool):
    """The complement: a newer `ingest` job that ended `'failed'` or `'dead'`
    never wrote any data, so it must NOT supersede — otherwise a
    permanently-broken upload would forever block the tenant's scheduled
    recompute from ever refreshing its data again."""
    with admin_pool.connection() as conn:
        tenant_id = _tenant(conn, "c5-guard-ignores-failed")
        old_job_id = _done_ingest(conn, tenant_id, {
            "tenant_id": tenant_id, "tenant_slug": "c5-guard-ignores-failed",
            "batch_id": "OLD", "files": {"parts": "old/parts"}, "uploaded_by": "u1",
        })
        _done_ingest(
            conn, tenant_id, {
                "tenant_id": tenant_id, "tenant_slug": "c5-guard-ignores-failed",
                "batch_id": "BROKEN", "files": {"parts": "broken/parts"}, "uploaded_by": "u1",
            },
            status="failed",
        )
        _done_ingest(
            conn, tenant_id, {
                "tenant_id": tenant_id, "tenant_slug": "c5-guard-ignores-failed",
                "batch_id": "DEAD", "files": {"parts": "dead/parts"}, "uploaded_by": "u1",
            },
            status="dead",
        )

    with admin_pool.connection() as conn:
        assert worker._superseded_reason(conn, tenant_id, old_job_id) is None


def test_superseded_reason_ignores_a_prior_recompute_row(admin_pool):
    """Mirrors `test_recompute_uses_the_upload_ingest_not_a_prior_recompute`
    at the guard level directly: a `kind='recompute'` row landing after
    `expected_job_id` is not an upload and must never itself trigger
    supersession — the guard filters `kind = 'ingest'` just like
    `_last_done_ingest` does."""
    with admin_pool.connection() as conn:
        tenant_id = _tenant(conn, "c5-guard-ignores-recompute")
        job_id = _done_ingest(conn, tenant_id, {
            "tenant_id": tenant_id, "tenant_slug": "c5-guard-ignores-recompute",
            "batch_id": "UPLOAD", "files": {"parts": "upload/parts"}, "uploaded_by": "u1",
        })
        _done_recompute(conn, tenant_id, {"source": "recompute"})

    with admin_pool.connection() as conn:
        assert worker._superseded_reason(conn, tenant_id, job_id) is None


# --- Fix round 1: real seed, real lock — no mocked run_ingest ---------------


def test_run_job_aborts_without_seeding_when_a_newer_ingest_lands_before_the_seed(
    admin_pool, monkeypatch
):
    """The residual reversion window this fix round closes, end to end: the
    recompute's resolution (step 2 below) is already done BEFORE a fresh
    upload-ingest for the SAME tenant commits (step 3) — strictly before the
    recompute's own seed attempt (step 4). Drives the REAL `run_ingest` (no
    monkeypatch of it) via `worker._run_job` exactly as `_recompute_handler`
    would call it after its own resolution, so this proves the actual seed
    is skipped — not merely that a mock was told to skip it. Must fail if
    the `guard` plumbing is ever removed: `_run_job` would then either raise
    (if `run_ingest` drops the parameter) or silently seed the stale OLD
    payload for real, reverting `part_keys` from the newer upload's 2 rows
    back down to 1 — either way the assertions below catch it.
    """
    monkeypatch.setattr(worker, "_ingest_pool", admin_pool, raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")

    slug = "c5-recompute-race-e2e"
    with admin_pool.connection() as conn:
        tenant_id = _tenant(conn, slug)

    old_payload = {
        "tenant_id": tenant_id, "tenant_slug": slug, "batch_id": "OLD",
        "uploaded_by": "u1",
        "files": {
            "parts": "old/parts", "stock": "old/stock", "demand_history": "old/demand",
            "vendors": "old/vendors",
        },
    }
    new_payload = {
        "tenant_id": tenant_id, "tenant_slug": slug, "batch_id": "NEW",
        "uploaded_by": "u1",
        "files": {
            "parts": "new/parts", "stock": "new/stock", "demand_history": "new/demand",
            "vendors": "new/vendors",
        },
    }
    old_blobs = {
        "old/parts": _PARTS_1, "old/stock": _STOCK_1, "old/demand": _DEMAND_1,
        "old/vendors": _VENDORS_1,
    }
    new_blobs = {
        "new/parts": _PARTS_2, "new/stock": _STOCK_2, "new/demand": _DEMAND_2,
        "new/vendors": _VENDORS_2,
    }

    # 1. The OLD ingest completes first and is recorded 'done' — this is
    #    what a recompute enqueued right now would resolve to replay.
    with admin_pool.connection() as conn:
        old_out = ingest_mod.run_ingest(
            conn, admin_pool, old_payload, storage=_FakeStorage(old_blobs), tenant_name="C",
        )
    assert old_out["status"] == "done"
    with admin_pool.connection() as conn:
        old_job_id = _done_ingest(conn, tenant_id, old_payload)
    with admin_pool.connection() as conn:
        assert _key_count(conn, tenant_id) == 1  # sanity: OLD's own seed landed

    # 2. `_recompute_handler`'s OWN resolution step, done explicitly here so
    #    the test can inject the race strictly AFTER it — the exact sequence
    #    the review fix is about.
    with admin_pool.connection() as conn:
        resolved = worker._last_done_ingest(conn, tenant_id)
    assert resolved == (old_job_id, old_payload)

    # 3. THE RACE: a fresh upload-ingest for the SAME tenant commits — its
    #    own real seed AND its own 'done' job row — strictly between that
    #    resolution and the seed attempt in step 4.
    with admin_pool.connection() as conn:
        new_out = ingest_mod.run_ingest(
            conn, admin_pool, new_payload, storage=_FakeStorage(new_blobs), tenant_name="C",
        )
    assert new_out["status"] == "done"
    with admin_pool.connection() as conn:
        _done_ingest(conn, tenant_id, new_payload)
    with admin_pool.connection() as conn:
        assert _key_count(conn, tenant_id) == 2  # sanity: the race's own seed landed

    # 4. NOW the already-resolved recompute's seed attempt runs — exactly
    #    what `_recompute_handler` does after step 2, with a guard bound to
    #    the now-STALE `old_job_id`. `HttpxStorageReader` is stubbed only for
    #    this call since `_run_job` builds it internally from env vars, with
    #    no injectable `storage` of its own.
    monkeypatch.setattr(
        worker, "HttpxStorageReader", lambda *a, **kw: _FakeStorage(old_blobs)
    )

    def _guard(conn):
        return worker._superseded_reason(conn, tenant_id, old_job_id)

    out = worker._run_job(old_payload, preserve=worker._RECOMPUTE_PRESERVE, guard=_guard)

    assert out["status"] == "superseded"
    assert out["result"]["outcome"] == "superseded"
    assert str(old_job_id) in out["result"]["reason"]

    with admin_pool.connection() as conn:
        assert _key_count(conn, tenant_id) == 2, (
            "the recompute must NOT revert the newer upload's 2-key state "
            "back down to the stale OLD payload's 1 key"
        )


def test_recompute_seeds_normally_when_nothing_supersedes_it(admin_pool, monkeypatch):
    """The complement to the test above: with NO race — the tenant's latest
    completed ingest is unchanged between resolution and seed — the
    recompute must proceed and seed exactly as before. Drives the full,
    real `worker.HANDLERS["recompute"]` entry point end to end (no
    monkeypatch of `run_ingest`, `_last_done_ingest`, or
    `_superseded_reason` — only the Storage layer is stubbed, same as the
    test above), proving the guard's mere PRESENCE does not get in the way
    of an ordinary recompute."""
    monkeypatch.setattr(worker, "_ingest_pool", admin_pool, raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")

    slug = "c5-recompute-normal-e2e"
    with admin_pool.connection() as conn:
        tenant_id = _tenant(conn, slug)

    payload = {
        "tenant_id": tenant_id, "tenant_slug": slug, "batch_id": "B1",
        "uploaded_by": "u1",
        "files": {
            "parts": "b1/parts", "stock": "b1/stock", "demand_history": "b1/demand",
            "vendors": "b1/vendors",
        },
    }
    blobs = {
        "b1/parts": _PARTS_2, "b1/stock": _STOCK_2, "b1/demand": _DEMAND_2,
        "b1/vendors": _VENDORS_2,
    }

    with admin_pool.connection() as conn:
        seed_out = ingest_mod.run_ingest(
            conn, admin_pool, payload, storage=_FakeStorage(blobs), tenant_name="C"
        )
    assert seed_out["status"] == "done"
    with admin_pool.connection() as conn:
        _done_ingest(conn, tenant_id, payload)
    with admin_pool.connection() as conn:
        assert _key_count(conn, tenant_id) == 2  # sanity: the upload's own seed landed

    monkeypatch.setattr(
        worker, "HttpxStorageReader", lambda *a, **kw: _FakeStorage(blobs)
    )

    out = worker.HANDLERS["recompute"]({"source": "recompute", "tenant_id": tenant_id})

    assert out["status"] == "done"
    assert out["result"]["source"] == "recompute"
    assert out["result"]["keys"] == 2

    with admin_pool.connection() as conn:
        assert _key_count(conn, tenant_id) == 2  # re-seeded, not lost or reverted
