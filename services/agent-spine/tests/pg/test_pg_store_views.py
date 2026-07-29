"""Seeded-view parity: pg reads == in-memory computes, before AND after decisions."""
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from trax_io_feature_store.schemas import LeadTimeDistribution

from trax_io_spine.bff.store import PlannerStore
from trax_io_spine.pg.seed import seed_store
from trax_io_spine.pg.store import PgPlannerStore
from trax_io_spine.planning_inputs import (
    planning_input_coverage,
    planning_input_source_generation_hash,
    planning_input_source_snapshot_hash,
)

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
    local_context = mem.part_context(ks.pn, ks.location)
    pg_context = pg.part_context(ks.pn, ks.location)
    assert pg_context == local_context
    assert pg_context.planning_trace == local_context.planning_trace
    assert pg_context.candidate_frontier == local_context.candidate_frontier
    assert pg_context.candidate_frontier is not None
    assert (
        pg_context.candidate_frontier.model_dump(mode="json")
        == local_context.candidate_frontier.model_dump(mode="json")
    )
    assert pg_context.procurement_lead_time == local_context.procurement_lead_time
    assert pg_context.repair_cycle_time == local_context.repair_cycle_time


def test_planning_input_snapshot_is_bulk_ordered_and_matches_o1_header(stores):
    mem, pg = stores
    universe = tuple(mem.part_context(pn, location) for pn, location in mem.keys)
    expected = tuple(
        sorted(
            (context for context in universe if context.candidate_frontier is not None),
            key=lambda context: context.candidate_frontier.decision_key,
        )
    )

    snapshot = pg.planning_input_snapshot()

    assert snapshot.contexts == expected
    assert snapshot.source_snapshot_hash == planning_input_source_snapshot_hash(
        universe,
        coverage=snapshot.coverage,
    )
    assert pg.current_planning_source_snapshot_hash() == (
        snapshot.source_snapshot_hash
    )
    assert snapshot.source_generation_hash == (
        planning_input_source_generation_hash(snapshot.source_snapshot_hash)
    )
    assert pg.current_planning_source_generation_hash() == (
        snapshot.source_generation_hash
    )
    assert pg.current_planning_model_profile() == (
        mem.current_planning_model_profile()
    )
    assert snapshot.coverage["total_key_count"] == len(mem.keys)
    assert snapshot.coverage["eligible_key_count"] == len(expected)
    assert snapshot.coverage["returned_key_count"] == len(expected)
    assert snapshot.coverage["candidate_count"] == sum(
        len(context.candidate_frontier.candidates) for context in expected
    )
    assert snapshot.seeded_at is not None


def test_explicit_planning_input_snapshot_preserves_scope_order_and_identity(
    stores,
):
    mem, pg = stores
    keys = tuple(reversed(mem.keys[:2]))

    snapshot = pg.planning_input_snapshot(keys)

    assert tuple((context.pn, context.location) for context in snapshot.contexts) == keys
    assert snapshot.source_snapshot_hash == planning_input_source_snapshot_hash(
        snapshot.contexts
    )
    assert snapshot.coverage["total_key_count"] == len(keys)
    assert snapshot.coverage["returned_key_count"] == len(keys)
    assert snapshot.source_snapshot_hash != pg.current_planning_source_snapshot_hash()


def test_planning_input_identity_includes_criticality(stores):
    mem, _pg = stores
    context = next(
        mem.part_context(*key)
        for key in mem.keys
        if mem.part_context(*key).candidate_frontier is not None
    )
    tier = context.attributes.criticality_tier
    changed_tier = 5 if tier != 5 else 4
    changed = context.model_copy(
        update={
            "attributes": context.attributes.model_copy(
                update={"criticality_tier": changed_tier}
            )
        }
    )

    assert planning_input_source_snapshot_hash((context,)) != (
        planning_input_source_snapshot_hash((changed,))
    )


def test_planning_input_identity_binds_excluded_key_universe(stores):
    mem, _pg = stores
    eligible = next(
        mem.part_context(*key)
        for key in mem.keys
        if mem.part_context(*key).candidate_frontier is not None
    )
    excluded_a = eligible.model_copy(
        update={
            "pn": "MISSING-A",
            "location": "MIA",
            "candidate_frontier": None,
        }
    )
    excluded_b = excluded_a.model_copy(update={"pn": "MISSING-B"})
    coverage_a = planning_input_coverage(
        (eligible, excluded_a),
        returned_key_count=1,
    )
    coverage_b = planning_input_coverage(
        (eligible, excluded_b),
        returned_key_count=1,
    )

    assert coverage_a == coverage_b
    assert planning_input_source_snapshot_hash(
        (eligible, excluded_a),
        coverage=coverage_a,
    ) != planning_input_source_snapshot_hash(
        (eligible, excluded_b),
        coverage=coverage_b,
    )


def test_pg_part_context_persists_independent_new_and_rep_lanes(
    admin_pool,
    pg_pool,
):
    mem = PlannerStore.from_extract(
        tenant_id="acme",
        extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    pn, location = "HYD-PUMP-001", "YYZ"
    for condition, mean in (("NEW", 19.0), ("REP", 57.0)):
        mem.fs.seed(
            "acme",
            "lead_time_distribution",
            (pn, "DEFAULT", condition),
            LeadTimeDistribution(
                tenant_id="acme",
                pn=pn,
                vendor="DEFAULT",
                condition=condition,
                realized_mean_days=mean,
                realized_p50_days=mean - 1,
                realized_p90_days=mean + 4,
                realized_p99_days=mean + 8,
                n_observations=15,
                extract_date=date(2026, 4, 1),
                evidence_status="observed",
                source="order_plan_closed_orders",
                grouping_level="part_condition",
                confidence="medium",
                data_cutoff=date(2026, 3, 31),
                model_version="supply-cycle-v1",
                proxy_definition=(
                    "order_creation_to_last_receipt"
                    if condition == "REP"
                    else None
                ),
                classification_source="explicit_order_type",
            ),
        )
    expected = mem.part_context(pn, location)
    report = seed_store(
        admin_pool,
        store=mem,
        slug="acme-supply-lanes",
        name="Acme Supply Lanes",
    )
    pg = PgPlannerStore(
        pg_pool,
        tenant_slug="acme-supply-lanes",
        tenant_uuid=report.tenant_uuid,
    )

    actual = pg.part_context(pn, location)

    assert actual.procurement_lead_time == expected.procurement_lead_time
    assert actual.procurement_lead_time.mean_days == 19
    assert actual.repair_cycle_time == expected.repair_cycle_time
    assert actual.repair_cycle_time.mean_days == 57
    assert actual.repair_cycle_time.proxy_label == "RO cycle-time proxy"
    assert actual.lead_time == expected.lead_time


def test_selected_recommendation_part_context_parity(stores):
    mem, pg = stores
    key = ("HYD-PUMP-001", "YYZ")
    selected = next(
        entry
        for entry in mem._entries.values()
        if (entry.rec.part_number, entry.rec.current_location) == key
        and entry.rec.policy is None
    )

    local_context = mem.part_context(
        *key,
        recommendation_id=selected.rec.recommendation_id,
    )
    pg_context = pg.part_context(
        *key,
        recommendation_id=selected.rec.recommendation_id,
    )

    assert pg_context == local_context
    assert pg_context.candidate_frontier == local_context.candidate_frontier
    assert pg_context.planning_trace.projected_demand == (
        selected.rec.calculation_evidence.projected_demand
    )


def test_pg_selected_recommendation_unknown_or_wrong_key_is_not_found(stores):
    mem, pg = stores
    key = ("HYD-PUMP-001", "YYZ")
    other = next(
        entry
        for entry in mem._entries.values()
        if (entry.rec.part_number, entry.rec.current_location) != key
    )

    for recommendation_id in ("unknown-rec", other.rec.recommendation_id):
        with pytest.raises(Exception) as mem_exc:
            mem.part_context(*key, recommendation_id=recommendation_id)
        with pytest.raises(mem_exc.type):
            pg.part_context(*key, recommendation_id=recommendation_id)


def test_pg_part_context_accepts_legacy_json_without_planning_trace(admin_pool, pg_pool):
    mem = PlannerStore.from_extract(
        tenant_id="acme",
        extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    report = seed_store(admin_pool, store=mem, slug="acme-legacy", name="Legacy Air")
    pn, location = mem.keys[0]
    with admin_pool.connection() as conn:
        raw = conn.execute(
            "select context from part_contexts "
            "where tenant_id = %s::uuid and pn = %s and location = %s",
            (report.tenant_uuid, pn, location),
        ).fetchone()[0]
        raw.pop("planning_trace")
        raw.pop("procurement_lead_time")
        raw.pop("repair_cycle_time")
        conn.execute(
            "update part_contexts set context = %s "
            "where tenant_id = %s::uuid and pn = %s and location = %s",
            (json.dumps(raw), report.tenant_uuid, pn, location),
        )

    pg = PgPlannerStore(
        pg_pool, tenant_slug="acme-legacy", tenant_uuid=report.tenant_uuid
    )
    context = pg.part_context(pn, location)
    trace = context.planning_trace

    assert trace.event_count_source == "unavailable"
    assert trace.warnings
    assert context.procurement_lead_time.condition == "NEW"
    assert context.procurement_lead_time.status == "unavailable"
    assert context.procurement_lead_time.mean_days is None
    assert context.repair_cycle_time.condition == "REP"
    assert context.repair_cycle_time.status == "unavailable"
    assert context.repair_cycle_time.mean_days is None


def test_pg_part_context_accepts_legacy_json_without_candidate_frontier(
    admin_pool,
    pg_pool,
):
    mem = PlannerStore.from_extract(
        tenant_id="acme",
        extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    report = seed_store(
        admin_pool,
        store=mem,
        slug="acme-legacy-frontier",
        name="Legacy Frontier Air",
    )
    pn, location = next(
        key
        for key in mem.keys
        if mem.part_context(*key).candidate_frontier is not None
    )
    with admin_pool.connection() as conn:
        raw = conn.execute(
            "select context from part_contexts "
            "where tenant_id = %s::uuid and pn = %s and location = %s",
            (report.tenant_uuid, pn, location),
        ).fetchone()[0]
        raw.pop("candidate_frontier")
        conn.execute(
            "update part_contexts set context = %s "
            "where tenant_id = %s::uuid and pn = %s and location = %s",
            (json.dumps(raw), report.tenant_uuid, pn, location),
        )

    pg = PgPlannerStore(
        pg_pool,
        tenant_slug="acme-legacy-frontier",
        tenant_uuid=report.tenant_uuid,
    )
    context = pg.part_context(pn, location)

    assert context.candidate_frontier is None
    assert context.planning_trace.calculation_source == "served_calculation"


def test_part_context_unknown_key_matches_memory(stores):
    mem, pg = stores
    with pytest.raises(Exception) as mem_exc:
        mem.part_context("NOPE", "ZZZ")
    with pytest.raises(mem_exc.type):
        pg.part_context("NOPE", "ZZZ")


def test_dashboard_parity_incl_live_fields(stores):
    mem, pg = stores
    assert pg.dashboard() == mem.dashboard()
    # Conservative open-order coverage may truthfully defer every writable policy.
    # Exercise a decision that is valid for every lifecycle status instead of
    # weakening that guardrail to manufacture a pending approval.
    rid = next(iter(mem._entries))
    mem.defer(rid)
    pg.defer(rid)
    assert pg.dashboard() == mem.dashboard()


def test_forecast_and_feeds_parity(stores):
    mem, pg = stores
    assert pg.forecast_summary() == mem.forecast_summary()
    assert pg.feeds_summary() == mem.feeds_summary()
