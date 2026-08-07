"""Ingest handler end-to-end: sample canonical CSVs → validate → engine → seed."""
import threading

import psycopg
import pytest

from tests.pg.conftest import as_tenant  # noqa: F401
from trax_io_spine.pg.ingest import _open_order_telemetry, run_ingest

T = "eeeeeeee-4444-4444-4444-eeeeeeee0c34"
PARTS = b"part_number,part_class,unit_cost,criticality\nP1,rotable,100,AOG\n"
STOCK = (b"part_number,location_code,on_hand,current_rop,current_eoq,"
         b"current_safety_stock,current_max\nP1,MIA,5,3,10,2,20\n")
DEMAND = (
    b"part_number,location_code,period,quantity,observation_start,observation_end\n"
    b"P1,MIA,2026-01-01,3,2025-01-01,2026-01-01\n"
)
# Without a `vendors` file, the (P1, MIA) key never gets a `vendor_economics`
# row and the recommendation engine silently routes it to `skipped` — `keys`
# lands at 1 but `recommendations` stays 0 (empirically confirmed). The tests
# below that need a NON-EMPTY `recommendations` table (to prove data survives,
# or to prove concurrent seeds don't double it) include this file so the engine
# actually emits an `ADJUST_MIN_MAX` recommendation for the sparse-demand key.
VENDORS = b"part_number,vendor_code,unit_price,lead_time_days\nP1,V1,100,14\n"
REPAIR_HISTORY = (
    b"repair_order_id,repair_line_id,part_number,quantity,started_at,completed_at,"
    b"status,shop_code,location_code,outcome,serial_number\n"
    b"RO-1,1,P1,1,2025-12-01,2026-01-01,completed,SHOP-1,MIA,serviceable,S-1\n"
    b"RO-2,1,P1,1,2025-12-10,2026-01-10,scrapped,SHOP-1,MIA,scrapped,S-2\n"
)


def test_open_order_telemetry_classifies_explicit_fallback_and_unknown() -> None:
    assert _open_order_telemetry(
        {
            "open_orders": [
                {"order_type": "PO", "order_id": "ignored"},
                {"order_type": "RO", "order_id": "ignored"},
                {"order_id": "PO-LEGACY"},
                {"order_id": "RO/LEGACY"},
                {"order_id": "AMBIGUOUS"},
                {"order_type": "UNKNOWN", "order_id": "PO-CONFLICT"},
            ]
        }
    ) == {
        "open_order_po_count": 2,
        "open_order_ro_count": 2,
        "open_order_unknown_count": 2,
        "open_order_legacy_fallback_count": 2,
    }


class FakeStorage:
    def __init__(self, blobs):
        self._blobs = blobs

    def download(self, path):
        return self._blobs[path]


@pytest.fixture()
def tenant(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name, key_quota) values (%s, 'acme-c3t4', 'A', 5000) "
            "on conflict (id) do nothing",
            (T,),
        )
        conn.commit()
    return T


def _payload(*, with_vendors: bool = False, with_repair_history: bool = False):
    files = {
        "parts": "acme-c3t4/b1/parts.csv", "stock": "acme-c3t4/b1/stock.csv",
        "demand_history": "acme-c3t4/b1/demand.csv",
    }
    if with_vendors:
        files["vendors"] = "acme-c3t4/b1/vendors.csv"
    if with_repair_history:
        files["repair_history"] = "acme-c3t4/b1/repair_history.csv"
    return {
        "tenant_id": T, "tenant_slug": "acme-c3t4", "batch_id": "b1",
        "files": files,
        "uploaded_by": "u1",
    }


def test_clean_ingest_seeds(tenant, admin_pool, pg_pool):
    storage = FakeStorage({
        "acme-c3t4/b1/parts.csv": PARTS,
        "acme-c3t4/b1/stock.csv": STOCK,
        "acme-c3t4/b1/demand.csv": DEMAND,
    })
    with admin_pool.connection() as conn:
        out = run_ingest(conn, pg_pool, _payload(), storage=storage, tenant_name="A")
    assert out["status"] == "done"
    assert out["result"]["keys"] >= 1
    # recommendations landed for the tenant
    with admin_pool.connection() as conn:
        n = conn.execute(
            "select count(*) from recommendations where tenant_id = %s::uuid", (T,)
        ).fetchone()[0]
    assert n == out["result"]["recommendations"]


def test_dirty_ingest_fails_without_seeding(tenant, admin_pool, pg_pool):
    bad_stock = b"part_number,location_code,on_hand\nP1,MIA,lots\n"
    storage = FakeStorage({
        "acme-c3t4/b1/parts.csv": PARTS,
        "acme-c3t4/b1/stock.csv": bad_stock,
        "acme-c3t4/b1/demand.csv": DEMAND,
    })
    with admin_pool.connection() as conn:
        # ensure no prior rows
        conn.execute("delete from recommendations where tenant_id = %s::uuid", (T,))
        conn.commit()
        out = run_ingest(conn, pg_pool, _payload(), storage=storage, tenant_name="A")
    assert out["status"] == "failed" and out["errors"]
    with admin_pool.connection() as conn:
        n = conn.execute(
            "select count(*) from recommendations where tenant_id = %s::uuid", (T,)
        ).fetchone()[0]
    assert n == 0


def test_dirty_ingest_preserves_prior_data(tenant, admin_pool, pg_pool):
    """Minor (C3 Task 4 review): a dirty ingest must not just avoid ADDING rows —
    it must leave the tenant's PRIOR seeded data intact. Asserting count == 0 (as
    `test_dirty_ingest_fails_without_seeding` does, against a manually-cleared
    table) would also pass if a dirty run wrongly wiped prior data as a side
    effect; this seeds real rows first and asserts they're still the exact rows
    present afterward."""
    storage = FakeStorage({
        "acme-c3t4/b1/parts.csv": PARTS,
        "acme-c3t4/b1/stock.csv": STOCK,
        "acme-c3t4/b1/demand.csv": DEMAND,
        "acme-c3t4/b1/vendors.csv": VENDORS,
    })
    with admin_pool.connection() as conn:
        clean_out = run_ingest(
            conn, pg_pool, _payload(with_vendors=True), storage=storage, tenant_name="A"
        )
    assert clean_out["status"] == "done"
    assert clean_out["result"]["recommendations"] >= 1  # sanity: the fixture actually fires

    with admin_pool.connection() as conn:
        before = sorted(
            conn.execute(
                "select rec_id from recommendations where tenant_id = %s::uuid", (T,)
            ).fetchall()
        )
    assert before  # sanity: the clean seed actually produced rows

    bad_stock = b"part_number,location_code,on_hand\nP1,MIA,lots\n"
    dirty_storage = FakeStorage({
        "acme-c3t4/b1/parts.csv": PARTS,
        "acme-c3t4/b1/stock.csv": bad_stock,
        "acme-c3t4/b1/demand.csv": DEMAND,
        "acme-c3t4/b1/vendors.csv": VENDORS,
    })
    with admin_pool.connection() as conn:
        dirty_out = run_ingest(
            conn, pg_pool, _payload(with_vendors=True), storage=dirty_storage, tenant_name="A"
        )
    assert dirty_out["status"] == "failed" and dirty_out["errors"]

    with admin_pool.connection() as conn:
        after = sorted(
            conn.execute(
                "select rec_id from recommendations where tenant_id = %s::uuid", (T,)
            ).fetchall()
        )
    assert after == before


def test_repair_history_ingest_reports_coverage_and_persists_independent_rep_lane(
    tenant,
    admin_pool,
    pg_pool,
):
    storage = FakeStorage(
        {
            "acme-c3t4/b1/parts.csv": PARTS,
            "acme-c3t4/b1/stock.csv": STOCK,
            "acme-c3t4/b1/demand.csv": DEMAND,
            "acme-c3t4/b1/vendors.csv": VENDORS,
            "acme-c3t4/b1/repair_history.csv": REPAIR_HISTORY,
        }
    )
    with admin_pool.connection() as conn:
        out = run_ingest(
            conn,
            pg_pool,
            _payload(with_vendors=True, with_repair_history=True),
            storage=storage,
            tenant_name="A",
        )

    assert out["status"] == "done"
    assert out["result"]["repair_history"] == {
        "accepted": 1,
        "excluded": 1,
        "quarantined": 0,
        "parts_covered": 1,
        "shops_covered": 1,
        "observed": 1,
        "pooled": 0,
        "proxy": 0,
        "unavailable": 0,
        "proxy_definition": "order_creation_to_last_receipt",
    }
    with admin_pool.connection() as conn:
        context = conn.execute(
            "select context from part_contexts "
            "where tenant_id = %s::uuid and pn = 'P1' and location = 'MIA'",
            (T,),
        ).fetchone()[0]
    assert context["procurement_lead_time"]["condition"] == "NEW"
    assert context["repair_cycle_time"]["condition"] == "REP"
    assert context["repair_cycle_time"]["n_observations"] == 1
    assert context["repair_cycle_time"]["proxy_label"] == "RO cycle-time proxy"
    assert out["_telemetry"] == {
        "open_order_po_count": 0,
        "open_order_ro_count": 0,
        "open_order_unknown_count": 0,
        "open_order_legacy_fallback_count": 0,
        "new_configured_fallback_count": 0,
        "new_unavailable_count": 1,
        "rep_configured_fallback_count": 0,
        "rep_unavailable_count": 0,
        "repair_duplicate_order_line_exclusion_count": 0,
        "repair_duplicate_serial_exclusion_count": 0,
    }


def test_invalid_repair_history_preserves_prior_tenant_snapshot(
    tenant,
    admin_pool,
    pg_pool,
):
    clean_storage = FakeStorage(
        {
            "acme-c3t4/b1/parts.csv": PARTS,
            "acme-c3t4/b1/stock.csv": STOCK,
            "acme-c3t4/b1/demand.csv": DEMAND,
            "acme-c3t4/b1/vendors.csv": VENDORS,
            "acme-c3t4/b1/repair_history.csv": REPAIR_HISTORY,
        }
    )
    payload = _payload(with_vendors=True, with_repair_history=True)
    with admin_pool.connection() as conn:
        clean = run_ingest(
            conn,
            pg_pool,
            payload,
            storage=clean_storage,
            tenant_name="A",
        )
    assert clean["status"] == "done"
    with admin_pool.connection() as conn:
        before = conn.execute(
            "select context from part_contexts "
            "where tenant_id = %s::uuid and pn = 'P1' and location = 'MIA'",
            (T,),
        ).fetchone()[0]

    invalid_repair = (
        b"repair_order_id,repair_line_id,part_number,quantity,started_at,completed_at,"
        b"status,shop_code,location_code,outcome,serial_number\n"
        b"RO-BAD,1,P1,1,2026-02-01,2026-01-01,completed,SHOP-1,MIA,serviceable,S-9\n"
    )
    dirty_storage = FakeStorage(
        {
            "acme-c3t4/b1/parts.csv": PARTS,
            "acme-c3t4/b1/stock.csv": STOCK,
            "acme-c3t4/b1/demand.csv": DEMAND,
            "acme-c3t4/b1/vendors.csv": VENDORS,
            "acme-c3t4/b1/repair_history.csv": invalid_repair,
        }
    )
    with admin_pool.connection() as conn:
        dirty = run_ingest(
            conn,
            pg_pool,
            payload,
            storage=dirty_storage,
            tenant_name="A",
        )

    assert dirty["status"] == "failed"
    assert any(
        error["file"] == "repair_history"
        and error["column"] == "completed_at"
        for error in dirty["errors"]
    )
    assert dirty["repair_history"] == {
        "accepted": 0,
        "excluded": 0,
        "quarantined": 1,
        "parts_covered": 0,
        "shops_covered": 0,
        "observed": 0,
        "pooled": 0,
        "proxy": 0,
        "unavailable": 1,
        "proxy_definition": "order_creation_to_last_receipt",
    }
    with admin_pool.connection() as conn:
        after = conn.execute(
            "select context from part_contexts "
            "where tenant_id = %s::uuid and pn = 'P1' and location = 'MIA'",
            (T,),
        ).fetchone()[0]
    assert after == before


# --- C3 Task 4 review (CRITICAL): per-tenant advisory lock serializes the seed ----


_REC_INSERT = (
    "insert into recommendations (tenant_id, rec_id, status, pn, location, tier,"
    " rec_type, criticality_tier, aog_level, confidence, cost_impact, priority,"
    " approvable, rec, outcome) values (%s::uuid, %s, 'pending', 'P1', 'MIA', 1,"
    " 'ADJUST_MIN_MAX', 1, 0, 0.9, 10.0, 1.0, true, '{}'::jsonb, '{}'::jsonb)"
)


def test_seed_replace_race_without_lock_doubles_rows(tenant, admin_pool):
    """Deterministic reproduction of the review's exact finding, modeled on
    `seed_store`'s DELETE-then-INSERT replace pattern on `recommendations`. Under
    Postgres READ COMMITTED, a second transaction's DELETE does NOT see a first
    transaction's still-uncommitted INSERT — so if two overlapping seeds are not
    serialized, transaction B's "clear the table" step misses transaction A's rows
    and both inserts survive, doubling the tenant's recommendations. No thread
    race is needed to prove this: hand-interleaving two open transactions'
    individual statements from a single test thread reproduces the exact same
    MVCC visibility gap deterministically — this is precisely the bug the
    per-tenant `pg_advisory_xact_lock` in `run_ingest` closes (proven blocking
    that interleaving in `test_advisory_lock_serializes_concurrent_seeds` below)."""
    # Start from a clean slate: if a row already existed here, A's DELETE would
    # take a row lock B's DELETE would then BLOCK on (waiting for A to commit or
    # roll back) instead of racing past it — which would hang this single-threaded
    # interleave forever, since nothing ever runs A's commit to release it.
    with admin_pool.connection() as conn:
        conn.execute("delete from recommendations where tenant_id = %s::uuid", (tenant,))
        conn.commit()

    with admin_pool.connection() as conn_a, admin_pool.connection() as conn_b:
        # Transaction A: replace-semantics delete + insert — NOT yet committed.
        conn_a.execute("delete from recommendations where tenant_id = %s::uuid", (tenant,))
        conn_a.execute(_REC_INSERT, (tenant, "rec-a"))
        # Transaction B interleaves BEFORE A commits: its delete (read committed)
        # can't see A's uncommitted insert, so A's row survives B's "clear" step.
        conn_b.execute("delete from recommendations where tenant_id = %s::uuid", (tenant,))
        conn_b.execute(_REC_INSERT, (tenant, "rec-b"))
        conn_a.commit()
        conn_b.commit()

    with admin_pool.connection() as conn:
        n = conn.execute(
            "select count(*) from recommendations where tenant_id = %s::uuid", (tenant,)
        ).fetchone()[0]
    assert n == 2  # doubled — exactly the bug the advisory lock exists to prevent

    # cleanup so later tests in this module see a clean slate for this tenant.
    with admin_pool.connection() as conn:
        conn.execute("delete from recommendations where tenant_id = %s::uuid", (tenant,))
        conn.commit()


def test_advisory_lock_serializes_concurrent_seeds(tenant, admin_pool):
    """Direct proof of the `pg_advisory_xact_lock` blocking semantics `run_ingest`
    relies on (mirrors `test_members.py`'s owner-lock proof): a second connection
    taking the SAME tenant-keyed advisory lock must BLOCK while a first connection
    holds it inside an open (uncommitted) transaction — proving the primitive
    actually serializes two overlapping seeds for the same tenant rather than
    letting their DELETE/INSERTs interleave."""
    with admin_pool.connection() as conn1:
        conn1.execute("select pg_advisory_xact_lock(hashtext(%s))", (tenant,))
        with admin_pool.connection() as conn2:
            conn2.execute("set lock_timeout = '300ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                conn2.execute("select pg_advisory_xact_lock(hashtext(%s))", (tenant,))
            conn2.rollback()
        conn1.rollback()


def test_concurrent_ingest_same_tenant_serializes_no_doubled_rows(tenant, admin_pool, pg_pool):
    """Integration-level companion to the two deterministic tests above: fires two
    REAL `run_ingest` calls for the SAME tenant concurrently, on separate threads
    and connections, through the actual code path (advisory lock + `seed_store`
    included) rather than a hand-modeled interleave. Python's GIL plus the CPU-bound
    parse/engine-run work means this doesn't reliably force the two transactions'
    SQL to overlap at the DB level the way the deterministic tests above do by
    construction — so on its own this test can't prove the race is fixed (it would
    likely pass even without the advisory lock, since real overlap rarely happens
    here). What it DOES prove: the real `run_ingest` path is safe and converges to
    exactly one copy under concurrent load, with no crashes — a regression net for
    the actual production code, complementing (not replacing) the deterministic
    proof in `test_seed_replace_race_without_lock_doubles_rows` +
    `test_advisory_lock_serializes_concurrent_seeds`."""
    storage = FakeStorage({
        "acme-c3t4/b1/parts.csv": PARTS,
        "acme-c3t4/b1/stock.csv": STOCK,
        "acme-c3t4/b1/demand.csv": DEMAND,
        "acme-c3t4/b1/vendors.csv": VENDORS,
    })
    payload = _payload(with_vendors=True)

    results: list[dict] = []
    errors: list[Exception] = []

    def _run():
        try:
            with admin_pool.connection() as conn:
                results.append(
                    run_ingest(conn, pg_pool, payload, storage=storage, tenant_name="A")
                )
        except Exception as exc:  # pragma: no cover - surfaced via `errors`
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, errors
    assert len(results) == 2
    assert all(r["status"] == "done" for r in results)
    counts = {r["result"]["recommendations"] for r in results}
    assert len(counts) == 1, "two identical clean runs should produce the same count"
    expected = counts.pop()
    assert expected >= 1  # sanity: the fixture actually produces rows to (not) double

    with admin_pool.connection() as conn:
        n = conn.execute(
            "select count(*) from recommendations where tenant_id = %s::uuid", (T,)
        ).fetchone()[0]
    assert n == expected, "concurrent seeds must serialize to ONE copy, not doubled"
