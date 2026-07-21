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


@pytest.fixture(scope="function")
def sample_store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )


def test_seed_store_writes_everything(admin_pool, sample_store):
    # Force real ledger content: approve a recommendation before seeding
    rid = next(r.recommendation_id for r in sample_store.queue() if r.approvable)
    sample_store.approve(rid)

    report = seed_store(
        admin_pool, store=sample_store, slug="acme-seed-test", name="Acme Seed Test"
    )
    assert report.recommendations == len(sample_store._entries)
    assert report.part_keys == len(sample_store._key_stats())
    assert report.part_contexts == report.part_keys
    # Verify ledger entries were captured and are > 0
    assert report.ledger_entries == len(
        sample_store.writeback.iter_history(sample_store.tenant_id)
    )
    assert report.ledger_entries > 0

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

        # Assert DB-side row counts for all 6 seeded tables
        assert conn.execute(
            "select count(*) from recommendations where tenant_id = %s::uuid",
            (report.tenant_uuid,),
        ).fetchone()[0] == report.recommendations
        assert conn.execute(
            "select count(*) from writeback_ledger where tenant_id = %s::uuid",
            (report.tenant_uuid,),
        ).fetchone()[0] == report.ledger_entries
        assert conn.execute(
            "select count(*) from part_keys where tenant_id = %s::uuid",
            (report.tenant_uuid,),
        ).fetchone()[0] == report.part_keys
        assert conn.execute(
            "select count(*) from part_contexts where tenant_id = %s::uuid",
            (report.tenant_uuid,),
        ).fetchone()[0] == report.part_contexts
        assert conn.execute(
            "select count(*) from tenant_snapshots where tenant_id = %s::uuid",
            (report.tenant_uuid,),
        ).fetchone()[0] == 4
        assert conn.execute(
            "select count(*) from kill_switches where tenant_id = %s::uuid",
            (report.tenant_uuid,),
        ).fetchone()[0] == 1


def test_seed_is_replace_idempotent(admin_pool, sample_store):
    r1 = seed_store(admin_pool, store=sample_store, slug="acme-seed-test", name="Acme Seed Test")
    r2 = seed_store(admin_pool, store=sample_store, slug="acme-seed-test", name="Acme Seed Test")
    assert (r1.tenant_uuid, r1.recommendations) == (r2.tenant_uuid, r2.recommendations)
    with admin_pool.connection() as conn:
        # Verify all 6 tables are replaced, not doubled
        tables_with_counts = [
            ("recommendations", r2.recommendations),
            ("writeback_ledger", r2.ledger_entries),
            ("part_keys", r2.part_keys),
            ("part_contexts", r2.part_contexts),
            ("tenant_snapshots", 4),
            ("kill_switches", 1),
        ]
        for table_name, expected_count in tables_with_counts:
            n = conn.execute(
                f"select count(*) from {table_name} where tenant_id = %s::uuid",  # noqa: S608
                (r2.tenant_uuid,),
            ).fetchone()[0]
            assert n == expected_count, (
                f"{table_name}: expected {expected_count}, got {n} (replaced, not doubled)"
            )
