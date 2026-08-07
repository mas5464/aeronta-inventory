"""Seed the committed sample snapshot into Postgres and assert row counts/shape.

Uses the same tiny sample data the BFF tests use (built via from_extract →
precompute pattern is heavyweight here, so we seed FROM a store built off the
sample extract, exercising the same code path from_snapshot_dir feeds into).
"""
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trax_io_spine.bff.store import PlannerStore
from trax_io_spine.pg.seed import (  # noqa: F401
    _context_operational_telemetry,
    seed_store,
    seed_tenant,
)
from trax_io_spine.pg.store import PgPlannerStore

EXTRACT = (
    Path(__file__).resolve().parents[3]
    / "recommendation-engine" / "examples" / "extract_sample"
)


def test_context_operational_telemetry_counts_lane_states_and_dedup() -> None:
    counts = _context_operational_telemetry(
        [
            {
                "procurement_lead_time": {
                    "status": "configured_fallback",
                },
                "repair_cycle_time": {"status": "unavailable"},
                "repair_pipeline": {
                    "exclusions": [
                        {"reason": "duplicate_order_line"},
                        {"reason": "duplicate_serial"},
                        {"reason": "duplicate_serial"},
                    ]
                },
            },
            {
                "procurement_lead_time": {"status": "unavailable"},
                "repair_cycle_time": {
                    "status": "configured_fallback",
                },
            },
        ]
    )

    assert counts == {
        "new_configured_fallback_count": 1,
        "new_unavailable_count": 1,
        "rep_configured_fallback_count": 1,
        "rep_unavailable_count": 1,
        "repair_duplicate_order_line_exclusion_count": 1,
        "repair_duplicate_serial_exclusion_count": 2,
    }


@pytest.fixture(scope="function")
def sample_store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )


def test_seed_store_writes_everything(admin_pool, sample_store):
    # Seed serialization needs real ledger content, but the sample's policy
    # recommendation may now be truthfully deferred by the open-order guardrail.
    # Exercise the writeback target directly instead of weakening that guardrail.
    entry = next(item for item in sample_store._entries.values() if item.rec.policy)
    sample_store.writeback.write(sample_store._req(entry.rec, entry.outcome))

    report = seed_store(
        admin_pool, store=sample_store, slug="acme-seed-test", name="Acme Seed Test"
    )
    assert report.recommendations == len(sample_store._entries)
    assert report.part_keys == len(sample_store.keys)
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
            "dashboard_static",
            "forecast_summary",
            "feeds_summary",
            "current_policies",
            "scenario_inputs",
            "planning_inputs",
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
        ).fetchone()[0] == 6
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
            ("tenant_snapshots", 6),
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


def test_seed_keeps_unavailable_demand_key_queryable_but_unscored(
    admin_pool, pg_pool, sample_store
):
    unavailable_key = sample_store.keys[0]
    demand_rows = sample_store.fs._data[sample_store.tenant_id]["demand_history"]
    demand_rows.pop(unavailable_key)
    sample_store._key_stats_cache = None

    scored_keys = {
        (stats.pn, stats.location) for stats in sample_store._key_stats()
    }
    assert unavailable_key not in scored_keys

    report = seed_store(
        admin_pool,
        store=sample_store,
        slug="acme-unavailable-demand",
        name="Acme Unavailable Demand",
    )
    assert report.part_keys == len(sample_store.keys)
    assert report.part_contexts == len(sample_store.keys)

    with admin_pool.connection() as conn:
        payload = conn.execute(
            "select key_stats from part_keys "
            "where tenant_id = %s::uuid and pn = %s and location = %s",
            (report.tenant_uuid, *unavailable_key),
        ).fetchone()[0]
        assert payload == {
            "pn": unavailable_key[0],
            "location": unavailable_key[1],
            "scorable": False,
        }
        assert conn.execute(
            "select count(*) from part_contexts "
            "where tenant_id = %s::uuid and pn = %s and location = %s",
            (report.tenant_uuid, *unavailable_key),
        ).fetchone()[0] == 1

    pg_store = PgPlannerStore(
        pg_pool,
        tenant_slug="acme-unavailable-demand",
        tenant_uuid=report.tenant_uuid,
    )
    assert pg_store.part_context(*unavailable_key).planning_trace.event_count_source == (
        "unavailable"
    )
    assert unavailable_key not in {
        (stats.pn, stats.location) for stats in pg_store._key_stats()
    }
