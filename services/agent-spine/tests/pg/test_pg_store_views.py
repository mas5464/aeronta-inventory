"""Seeded-view parity: pg reads == in-memory computes, before AND after decisions."""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trax_io_spine.bff.store import PlannerStore
from trax_io_spine.pg.seed import seed_store
from trax_io_spine.pg.store import PgPlannerStore

EXTRACT = (
    Path(__file__).resolve().parents[3]
    / "recommendation-engine" / "examples" / "extract_sample"
)


@pytest.fixture()
def stores(admin_pool, pg_pool):
    mem = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    report = seed_store(admin_pool, store=mem, slug="acme-t11", name="Acme Air")
    return mem, PgPlannerStore(pg_pool, tenant_slug="acme-t11", tenant_uuid=report.tenant_uuid)


def test_part_context_parity(stores):
    mem, pg = stores
    ks = mem._key_stats()[0]
    assert pg.part_context(ks.pn, ks.location) == mem.part_context(ks.pn, ks.location)


def test_part_context_unknown_key_matches_memory(stores):
    mem, pg = stores
    with pytest.raises(Exception) as mem_exc:
        mem.part_context("NOPE", "ZZZ")
    with pytest.raises(mem_exc.type):
        pg.part_context("NOPE", "ZZZ")


def test_dashboard_parity_incl_live_fields(stores):
    mem, pg = stores
    assert pg.dashboard() == mem.dashboard()
    rid = next(r.recommendation_id for r in mem.queue() if r.approvable)
    mem.approve(rid)
    pg.approve(rid)
    assert pg.dashboard() == mem.dashboard()


def test_forecast_and_feeds_parity(stores):
    mem, pg = stores
    assert pg.forecast_summary() == mem.forecast_summary()
    assert pg.feeds_summary() == mem.feeds_summary()
