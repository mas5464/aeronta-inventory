"""Offline seeder: an in-memory PlannerStore -> Postgres rows (C1 Task 8).

The in-memory store IS the computation engine; this module only serializes its
outputs. `seed_tenant` (snapshot-dir entry point, used by the CLI/deploy) and
`seed_store` (store entry point, used by tests and later by C3's ingest job)
share one code path. Replace-semantics per tenant, single transaction.

Runs on a BYPASSRLS pool (trax_seed) — the sanctioned service path (spec §3);
per-key part_context serialization is O(keys) and offline by design.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from datetime import UTC, datetime

from trax_io_spine.bff.store import PlannerStore


@dataclasses.dataclass(frozen=True)
class SeedReport:
    tenant_uuid: str
    recommendations: int
    ledger_entries: int
    part_keys: int
    part_contexts: int


def _dump(model) -> str:
    return json.dumps(model.model_dump(mode="json"))


_SEEDED_TABLES = (
    "recommendations", "writeback_ledger", "part_keys", "part_contexts",
    "tenant_snapshots", "kill_switches",
)


def seed_store(pool, *, store: PlannerStore, slug: str, name: str) -> SeedReport:
    with pool.connection() as conn:
        row = conn.execute(
            "insert into tenants (slug, name) values (%s, %s) "
            "on conflict (slug) do update set name = excluded.name returning id::text",
            (slug, name),
        ).fetchone()
        tenant_uuid = row[0]
        for table in _SEEDED_TABLES:
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
        conn.cursor().executemany(
            "insert into recommendations (tenant_id, rec_id, status, pn, location, tier,"
            " rec_type, criticality_tier, aog_level, confidence, cost_impact, priority,"
            " approvable, rec, outcome)"
            " values (%s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            rec_rows,
        )

        ledger = store.writeback.iter_history(store.tenant_id)
        conn.cursor().executemany(
            "insert into writeback_ledger (tenant_id, pn, location, version, entry,"
            " changed_at) values (%s::uuid, %s, %s, %s, %s, %s)",
            [(tenant_uuid, e.pn, e.location, e.version, _dump(e), e.changed_at)
             for e in ledger],
        )

        key_stats = store._key_stats()
        conn.cursor().executemany(
            "insert into part_keys (tenant_id, pn, location, key_stats)"
            " values (%s::uuid, %s, %s, %s)",
            [(tenant_uuid, ks.pn, ks.location, json.dumps(dataclasses.asdict(ks))) for ks in key_stats],
        )
        contexts = [
            (tenant_uuid, ks.pn, ks.location, _dump(store.part_context(ks.pn, ks.location)))
            for ks in key_stats
        ]
        conn.cursor().executemany(
            "insert into part_contexts (tenant_id, pn, location, context)"
            " values (%s::uuid, %s, %s, %s)",
            contexts,
        )

        policies = {}
        for ks in key_stats:
            pol = store.fs.get_current_policy(
                tenant=store.tenant, pn=ks.pn, location=ks.location
            ) if store.fs else None
            if pol is not None:
                policies[f"{ks.pn}|{ks.location}"] = {
                    "rop": pol.rop, "eoq": pol.eoq,
                    "safety_stock": pol.safety_stock, "max_stock": pol.max_stock,
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
        ]
        conn.cursor().executemany(
            "insert into tenant_snapshots (tenant_id, kind, payload)"
            " values (%s::uuid, %s, %s)",
            [(tenant_uuid, kind, payload) for kind, payload in snapshots],
        )
        conn.execute(
            "insert into kill_switches (tenant_id, engaged) values (%s::uuid, %s)",
            (tenant_uuid, store.kill_switch),
        )
        conn.commit()
        return SeedReport(
            tenant_uuid=tenant_uuid, recommendations=len(rec_rows),
            ledger_entries=len(ledger), part_keys=len(key_stats),
            part_contexts=len(contexts),
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
