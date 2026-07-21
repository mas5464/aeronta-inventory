"""Seed the committed sample snapshot into Postgres and assert row counts/shape.

Uses the same tiny sample data the BFF tests use (built via from_extract →
precompute pattern is heavyweight here, so we seed FROM a store built off the
sample extract, exercising the same code path from_snapshot_dir feeds into).
"""
from datetime import UTC, datetime
from pathlib import Path

import pytest
from trax_io_spine.bff.store import PlannerStore

from trax_io_spine.pg.seed import seed_store, seed_tenant  # noqa: F401

EXTRACT = (
    Path(__file__).resolve().parents[3]
    / "recommendation-engine" / "examples" / "extract_sample"
)


@pytest.fixture(scope="module")
def sample_store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )


def test_seed_store_writes_everything(admin_pool, sample_store):
    report = seed_store(admin_pool, store=sample_store, slug="acme-seed-test", name="Acme Seed Test")
    assert report.recommendations == len(sample_store._entries)
    assert report.part_keys == len(sample_store._key_stats())
    assert report.part_contexts == report.part_keys
    with admin_pool.connection() as conn:
        kinds = {
            r[0] for r in conn.execute(
                "select kind from tenant_snapshots where tenant_id = %s::uuid",
                (report.tenant_uuid,),
            ).fetchall()
        }
        assert kinds == {
            "dashboard_static", "forecast_summary", "feeds_summary", "current_policies"
        }


def test_seed_is_replace_idempotent(admin_pool, sample_store):
    r1 = seed_store(admin_pool, store=sample_store, slug="acme-seed-test", name="Acme Seed Test")
    r2 = seed_store(admin_pool, store=sample_store, slug="acme-seed-test", name="Acme Seed Test")
    assert (r1.tenant_uuid, r1.recommendations) == (r2.tenant_uuid, r2.recommendations)
    with admin_pool.connection() as conn:
        n = conn.execute(
            "select count(*) from recommendations where tenant_id = %s::uuid",
            (r2.tenant_uuid,),
        ).fetchone()[0]
        assert n == r2.recommendations  # replaced, not doubled
