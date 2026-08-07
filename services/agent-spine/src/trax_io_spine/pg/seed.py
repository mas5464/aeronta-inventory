"""Offline seeder: an in-memory PlannerStore -> Postgres rows (C1 Task 8).

The in-memory store IS the computation engine; this module only serializes its
outputs. `seed_tenant` (snapshot-dir entry point, used by the CLI/deploy) and
`seed_store` (store entry point, used by tests, C3's ingest job, and C5's
nightly recompute) share one code path. Replace-semantics per tenant, single
transaction — except for any table named in `seed_store`'s `preserve` kwarg
(C5), which is left completely alone (no delete, no reinsert).

Runs on a BYPASSRLS pool (trax_seed) — the sanctioned service path (spec §3);
per-key part_context plus per-recommendation trace serialization is offline by
design.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from datetime import UTC, datetime

from trax_io_spine.bff.store import PlannerStore, _safe
from trax_io_spine.planning_inputs import (
    PLANNING_INPUTS_CONTRACT_VERSION,
    planning_input_coverage,
    planning_input_model_profile,
    planning_input_source_generation_hash,
    planning_input_source_snapshot_hash,
)
from trax_io_spine.scenario_result import scenario_inputs_payload


@dataclasses.dataclass(frozen=True)
class SeedReport:
    tenant_uuid: str
    recommendations: int
    ledger_entries: int
    part_keys: int
    part_contexts: int
    operational_telemetry: dict[str, int]


def _dump(model) -> str:
    return json.dumps(model.model_dump(mode="json"))


_PLANNING_TRACES_BY_RECOMMENDATION = "_planning_traces_by_recommendation"
_SCORABLE_KEY = "scorable"


def _context_operational_telemetry(
    contexts: list[dict],
) -> dict[str, int]:
    """Aggregate bounded Phase 12 lane/dedup counters during existing work."""

    counts = {
        "new_configured_fallback_count": 0,
        "new_unavailable_count": 0,
        "rep_configured_fallback_count": 0,
        "rep_unavailable_count": 0,
        "repair_duplicate_order_line_exclusion_count": 0,
        "repair_duplicate_serial_exclusion_count": 0,
    }
    for context in contexts:
        for field, prefix in (
            ("procurement_lead_time", "new"),
            ("repair_cycle_time", "rep"),
        ):
            lane = context.get(field)
            status = lane.get("status") if isinstance(lane, dict) else None
            if status == "configured_fallback":
                counts[f"{prefix}_configured_fallback_count"] += 1
            elif status == "unavailable":
                counts[f"{prefix}_unavailable_count"] += 1

        pipeline = context.get("repair_pipeline")
        exclusions = (
            pipeline.get("exclusions", [])
            if isinstance(pipeline, dict)
            else ()
        )
        if not isinstance(exclusions, (list, tuple)):
            continue
        for exclusion in exclusions:
            reason = (
                exclusion.get("reason")
                if isinstance(exclusion, dict)
                else None
            )
            if reason == "duplicate_order_line":
                counts["repair_duplicate_order_line_exclusion_count"] += 1
            elif reason == "duplicate_serial":
                counts["repair_duplicate_serial_exclusion_count"] += 1
    return counts


def _part_context_document(
    store: PlannerStore,
    pn: str,
    location: str,
) -> dict:
    """Persist the compatible default context plus exact per-recommendation traces.

    The internal trace map is stripped by ``PgPlannerStore`` before validating the
    public ``PartContext``. Keeping it beside the source context makes a selected
    recommendation read exact in Postgres without recomputing forecasts online.
    """
    context = store.part_context(pn, location).model_dump(mode="json")
    matching_ids = sorted(
        rec_id
        for rec_id, entry in store._entries.items()
        if (entry.rec.part_number, entry.rec.current_location) == (pn, location)
    )
    context[_PLANNING_TRACES_BY_RECOMMENDATION] = {
        rec_id: store.part_context(
            pn,
            location,
            recommendation_id=rec_id,
        ).planning_trace.model_dump(mode="json")
        for rec_id in matching_ids
    }
    return context


def _part_context_payload(store: PlannerStore, pn: str, location: str) -> str:
    return json.dumps(_part_context_document(store, pn, location))


def _key_payload(pn: str, location: str, key_stats_by_key: dict[tuple[str, str], object]) -> str:
    """Serialize one portfolio key without inventing scenario inputs.

    ``part_keys`` is both the tenant's billable/queryable key universe and the
    source for scenario primitives.  Keys with unavailable demand belong in the
    former but not the latter.  Persist an explicit sentinel that the PG reader
    filters before ``KeyStats`` validation rather than fabricating zero demand.
    """
    stats = key_stats_by_key.get((pn, location))
    if stats is None:
        return json.dumps(
            {
                "pn": pn,
                "location": location,
                _SCORABLE_KEY: False,
            }
        )
    return json.dumps(dataclasses.asdict(stats))


_SEEDED_TABLES = (
    "recommendations", "writeback_ledger", "part_keys", "part_contexts",
    "tenant_snapshots", "kill_switches",
)


def seed_store(
    pool, *, store: PlannerStore, slug: str, name: str,
    preserve: frozenset[str] = frozenset(),
) -> SeedReport:
    """Replace a tenant's seeded rows with `store`'s current contents.

    `preserve` names tables (from `_SEEDED_TABLES`) to leave untouched instead
    of delete-then-reinsert — e.g. C5's nightly recompute passes
    `{"writeback_ledger", "kill_switches"}` so a scheduled re-seed can never
    destroy the append-only audit ledger (rollback + SOC 2 evidence) or reset
    an operator's kill switch (a safety control). The set is caller-supplied
    on purpose: this module hardcodes no caller's policy. C3's upload-ingest
    (`ingest.run_ingest`) passes no `preserve` and keeps its original
    full-replace behavior byte-for-byte.

    An unrecognized name in `preserve` (typo, or a table `seed_store` never
    seeds in the first place, e.g. `decisions`) is rejected loudly rather than
    silently ignored — a caller asking to preserve something that was never
    going to be touched is almost always a mistake worth surfacing, not a
    no-op worth swallowing.
    """
    unknown = preserve - set(_SEEDED_TABLES)
    if unknown:
        raise ValueError(
            f"seed_store: preserve names unknown table(s): {sorted(unknown)}; "
            f"expected a subset of {_SEEDED_TABLES}"
        )
    with pool.connection() as conn:
        row = conn.execute(
            "insert into tenants (slug, name) values (%s, %s) "
            "on conflict (slug) do update set name = excluded.name returning id::text",
            (slug, name),
        ).fetchone()
        tenant_uuid = row[0]
        # C5: a scheduled recompute must never delete the append-only
        # writeback ledger (rollback + SOC 2 audit) or reset the kill switch
        # (a safety control). Upload-ingest passes no `preserve` and keeps
        # full-replace semantics exactly as before.
        #
        # A preserved table is left FULLY untouched this pass, not just spared
        # the delete: below, each table's insert is symmetrically skipped when
        # preserved too. Without that, a preserved-but-still-inserted
        # `kill_switches` row (tenant_id is its primary key) would raise a
        # duplicate-key error on the very next seed, and a preserved-but-
        # reinserted table would either crash the same way or silently
        # overwrite the row the caller asked to keep — exactly what `preserve`
        # exists to prevent.
        for table in _SEEDED_TABLES:
            if table in preserve:
                continue
            conn.execute(  # noqa: S608 — table names from a module constant
                f"delete from {table} where tenant_id = %s::uuid", (tenant_uuid,)
            )

        rec_rows = []
        for entry in store._entries.values():
            rec, outcome = entry.rec, entry.outcome
            rec_rows.append((
                tenant_uuid, rec.recommendation_id, entry.status.value,
                rec.part_number, rec.current_location, int(outcome.tier),
                str(rec.type), int(rec.criticality_tier), int(rec.aog_risk_level),
                float(rec.confidence_score), float(rec.estimated_cost_impact),
                float(store._priority(entry)), rec.policy is not None,
                _dump(rec), _dump(outcome),
            ))
        if "recommendations" not in preserve:
            conn.cursor().executemany(
                "insert into recommendations (tenant_id, rec_id, status, pn, location, tier,"
                " rec_type, criticality_tier, aog_level, confidence, cost_impact, priority,"
                " approvable, rec, outcome)"
                " values (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                rec_rows,
            )

        ledger = store.writeback.iter_history(store.tenant_id)
        if "writeback_ledger" not in preserve:
            conn.cursor().executemany(
                "insert into writeback_ledger (tenant_id, pn, location, version, entry,"
                " changed_at) values (%s::uuid, %s, %s, %s, %s, %s)",
                [(tenant_uuid, e.pn, e.location, e.version, _dump(e), e.changed_at)
                 for e in ledger],
            )

        planning_keys = list(store.keys)
        key_stats = store._key_stats()
        key_stats_by_key = {(stats.pn, stats.location): stats for stats in key_stats}
        if "part_keys" not in preserve:
            conn.cursor().executemany(
                "insert into part_keys (tenant_id, pn, location, key_stats)"
                " values (%s::uuid, %s, %s, %s)",
                [
                    (
                        tenant_uuid,
                        pn,
                        location,
                        _key_payload(pn, location, key_stats_by_key),
                    )
                    for pn, location in planning_keys
                ],
            )
        context_documents = [
            _part_context_document(store, pn, location)
            for pn, location in planning_keys
        ]
        contexts = [
            (
                tenant_uuid,
                pn,
                location,
                json.dumps(context),
            )
            for (pn, location), context in zip(
                planning_keys,
                context_documents,
                strict=True,
            )
        ]
        if "part_contexts" not in preserve:
            conn.cursor().executemany(
                "insert into part_contexts (tenant_id, pn, location, context)"
                " values (%s::uuid, %s, %s, %s)",
                contexts,
            )

        policies = {}
        for pn, location in planning_keys:
            pol = (
                _safe(lambda pn=pn, location=location: store.fs.get_current_policy(
                    tenant=store.tenant, pn=pn, location=location))
                if store.fs else None
            )
            if pol is not None:
                policies[f"{pn}|{location}"] = {
                    "rop": pol.rop, "eoq": pol.eoq,
                    "safety_stock": pol.safety_stock, "max_stock": pol.max_stock,
                }
        eligible_contexts = tuple(
            context
            for context in context_documents
            if context.get("candidate_frontier") is not None
        )
        planning_inputs_coverage = planning_input_coverage(
            context_documents,
            total_key_count=len(planning_keys),
            returned_key_count=len(eligible_contexts),
        )
        planning_source_snapshot_hash = planning_input_source_snapshot_hash(
            context_documents,
            coverage=planning_inputs_coverage,
        )
        planning_input_summary = {
            "contract_version": PLANNING_INPUTS_CONTRACT_VERSION,
            "source_snapshot_hash": planning_source_snapshot_hash,
            "source_generation_hash": planning_input_source_generation_hash(
                planning_source_snapshot_hash
            ),
            "coverage": planning_inputs_coverage,
            "model_profile": planning_input_model_profile(context_documents),
        }
        snapshots = [
            ("dashboard_static", _dump(store.dashboard())),
            ("forecast_summary", _dump(store.forecast_summary())),
            ("feeds_summary", _dump(store.feeds_summary())),
            ("current_policies", json.dumps(
                {"policies": policies, "keys_total": len(store.keys),
                 "extract_date": store._manifest.get("extract_date"),
                 "seeded_at": datetime.now(UTC).isoformat()}
            )),
            (
                "scenario_inputs",
                json.dumps(
                    scenario_inputs_payload(
                        source_tenant_id=store.tenant_id,
                        source_manifest=store._manifest,
                        key_universe=planning_keys,
                        procurement_inputs=key_stats,
                        repair_inputs=store._repair_scenario_inputs(),
                    )
                ),
            ),
            ("planning_inputs", json.dumps(planning_input_summary)),
        ]
        if "tenant_snapshots" not in preserve:
            conn.cursor().executemany(
                "insert into tenant_snapshots (tenant_id, kind, payload)"
                " values (%s::uuid, %s, %s)",
                [(tenant_uuid, kind, payload) for kind, payload in snapshots],
            )
        if "kill_switches" not in preserve:
            conn.execute(
                "insert into kill_switches (tenant_id, engaged) values (%s::uuid, %s)",
                (tenant_uuid, store.kill_switch),
            )
        conn.commit()
        return SeedReport(
            tenant_uuid=tenant_uuid, recommendations=len(rec_rows),
            ledger_entries=len(ledger), part_keys=len(planning_keys),
            part_contexts=len(contexts),
            operational_telemetry=_context_operational_telemetry(
                context_documents
            ),
        )


def seed_tenant(pool, *, slug: str, name: str, snapshot_dir: str) -> SeedReport:
    store = PlannerStore.from_snapshot_dir(tenant_id=slug, snapshot_dir=snapshot_dir)
    return seed_store(pool, store=store, slug=slug, name=name)


def main() -> None:
    from .db import make_pool

    p = argparse.ArgumentParser(prog="trax-io-pg-seed")
    p.add_argument("--database-url", required=True)
    p.add_argument("--tenant", required=True, help="tenant slug, e.g. acme")
    p.add_argument("--name", required=True)
    p.add_argument("--snapshot-dir", required=True)
    args = p.parse_args()
    pool = make_pool(args.database_url)
    report = seed_tenant(
        pool, slug=args.tenant, name=args.name, snapshot_dir=args.snapshot_dir
    )
    print(dataclasses.asdict(report))


if __name__ == "__main__":
    main()
