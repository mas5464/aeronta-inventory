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
`_last_done_ingest_payload`). A race can then, at worst, replay data that is
NEWER than what was known at enqueue time — never older.

`test_recompute_replays_the_newest_completed_ingest_not_the_job_payload` is
the regression guard for exactly this: it must fail if this handler (or its
resolution query) ever goes back to trusting `job["payload"]` directly.
"""
from __future__ import annotations

import json

from trax_io_spine.pg import worker

# --- fixtures (raw SQL — mirrors test_c5_enqueue_recomputes.py / test_worker.py) --


def _tenant(conn, slug: str) -> str:
    row = conn.execute(
        "insert into tenants (slug, name) values (%s, %s) "
        "on conflict (slug) do update set name = excluded.name returning id::text",
        (slug, slug),
    ).fetchone()
    conn.commit()
    return row[0]


def _done_ingest(conn, tenant_id: str, payload: dict) -> None:
    conn.execute(
        "insert into jobs (tenant_id, kind, payload, status) "
        "values (%s::uuid, 'ingest', %s::jsonb, 'done')",
        (tenant_id, json.dumps(payload)),
    )
    conn.commit()


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

    def _fake_run_ingest(conn, pool, payload, *, storage, tenant_name="", preserve=frozenset()):
        seen["payload"] = payload
        seen["preserve"] = preserve
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
    assert out["result"]["source"] == "recompute"
    assert out["result"]["keys"] == 3


def test_recompute_uses_the_upload_ingest_not_a_prior_recompute(admin_pool, monkeypatch):
    """A previously-run `recompute` job is itself `kind='recompute'` — never
    `kind='ingest'`. `_last_done_ingest_payload` must keep resolving the
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

    def _fake_run_ingest(conn, pool, payload, *, storage, tenant_name="", preserve=frozenset()):
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
