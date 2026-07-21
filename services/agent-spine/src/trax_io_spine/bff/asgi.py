"""ASGI entrypoint for deploying the Planner-UI BFF.

Seeds a store for one tenant and exposes the FastAPI app for uvicorn. Deploy-only
— keeps `create_planner_app` pure. Config via env:
  PLANNER_TENANT       tenant id to seed     (default: acme)
  DATABASE_URL         Postgres connection string. When set, boots a `PgPlannerStore`
                       against it instead of any in-memory store below: builds a pool,
                       resolves PLANNER_TENANT's slug to a tenant uuid (the tenant must
                       already be seeded — run `trax-io-pg-seed` first), and serves off
                       Postgres. Highest precedence — none of the paths below run.
                       Must be a role that can read public.tenants pre-claims (e.g.
                       trax_seed, bypassrls) — the tenants_select RLS policy is scoped
                       to current_tenant_id(), which isn't known until AFTER this slug
                       lookup resolves it, so a plain trax_app-role URL can't bootstrap
                       itself (verified locally; see Task 13 report). Once resolved,
                       PgPlannerStore still SET LOCALs per-request tenant claims via
                       tenant_conn for every subsequent query.
  PLANNER_SNAPSHOT_DIR path to a COMPLETE precomputed snapshot dir (feature store +
                       keys + manifest + recs — see bff/precompute.py). When set (and
                       no DATABASE_URL), seeds via `PlannerStore.from_snapshot_dir`: no
                       extract parsing, no pooling, no engine at boot. Takes precedence
                       over the two paths below; the extract dir is not needed at all.
  PLANNER_RECS_FILE    path to a precomputed recs.json only. When set (and no
                       DATABASE_URL/PLANNER_SNAPSHOT_DIR), seeds via
                       `PlannerStore.from_snapshot`: skips the engine but still
                       rebuilds the feature store from EXTRACT_DIR.
  EXTRACT_DIR          path to the extract dir (default: examples/extract_sample,
                       relative to CWD)
  PLANNER_NOW          ISO 'now' for the run  (default: 2026-04-01T00:00:00+00:00)
  PLANNER_POOL_BY_PART truthy for real eMRO extracts (network-pooled on-hand/demand)
  PLANNER_PROJECTOR    "statistical" or "historical" (default) — from_extract only

Precedence: DATABASE_URL > PLANNER_SNAPSHOT_DIR > PLANNER_RECS_FILE > EXTRACT_DIR.

Env is read inside `build_app()` (not at module import) so tests can exercise the
precedence; the module-level `app = build_app()` below is the uvicorn entrypoint.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.store import PlannerStore


def build_app():
    tenant = os.environ.get("PLANNER_TENANT", "acme")
    database_url = os.environ.get("DATABASE_URL", "").strip() or None
    snapshot_dir = os.environ.get("PLANNER_SNAPSHOT_DIR", "").strip() or None
    recs_file = os.environ.get("PLANNER_RECS_FILE", "").strip() or None
    extract_dir = os.environ.get("EXTRACT_DIR", "examples/extract_sample")
    now = datetime.fromisoformat(
        os.environ.get("PLANNER_NOW", "2026-04-01T00:00:00+00:00")
    ).astimezone(UTC)
    pool_by_part = (
        os.environ.get("PLANNER_POOL_BY_PART", "").strip().lower() in {"1", "true", "yes"}
    )
    use_statistical = (
        os.environ.get("PLANNER_PROJECTOR", "historical").strip().lower() == "statistical"
    )

    if database_url:
        from trax_io_spine.pg.db import make_pool, resolve_tenant_uuid
        from trax_io_spine.pg.store import PgPlannerStore

        pool = make_pool(database_url)
        with pool.connection() as _conn:
            tenant_uuid = resolve_tenant_uuid(_conn, tenant)
        if tenant_uuid is None:
            raise RuntimeError(
                f"DATABASE_URL set but tenant {tenant!r} not found — run trax-io-pg-seed first"
            )
        store = PgPlannerStore(pool, tenant_slug=tenant, tenant_uuid=tenant_uuid)
        return create_planner_app({tenant: store})

    if snapshot_dir:
        store = PlannerStore.from_snapshot_dir(tenant_id=tenant, snapshot_dir=snapshot_dir)
    elif recs_file:
        store = PlannerStore.from_snapshot(
            tenant_id=tenant,
            extract_dir=extract_dir,
            recs_file=recs_file,
            now=now,
            pool_by_part=pool_by_part,
        )
    else:
        store = PlannerStore.from_extract(
            tenant_id=tenant,
            extract_dir=extract_dir,
            now=now,
            pool_by_part=pool_by_part,
            use_statistical=use_statistical,
        )
    return create_planner_app({tenant: store})


app = build_app()
