"""Spec §12 benchmark gate: full-network seed must serve interactive reads <1s.
Env-gated (needs the gitignored 58.9K-key snapshot); skips clean otherwise."""
import os
import time

import pytest

SNAPSHOT = os.environ.get("PG_BENCH_SNAPSHOT_DIR")


@pytest.mark.skipif(not SNAPSHOT, reason="PG_BENCH_SNAPSHOT_DIR not set")
def test_full_network_read_latency(admin_pool, pg_pool):
    from trax_io_spine.pg.seed import seed_tenant
    from trax_io_spine.pg.store import PgPlannerStore

    report = seed_tenant(admin_pool, slug="bench", name="Bench", snapshot_dir=SNAPSHOT)
    store = PgPlannerStore(pg_pool, tenant_slug="bench", tenant_uuid=report.tenant_uuid)
    t0 = time.perf_counter()
    rows, total = store.list_queue_page(limit=50)
    t1 = time.perf_counter()
    store.dashboard()
    t2 = time.perf_counter()
    assert rows and total > 10_000
    assert t1 - t0 < 1.0, f"queue page took {t1 - t0:.2f}s"
    assert t2 - t1 < 1.0, f"dashboard took {t2 - t1:.2f}s"
