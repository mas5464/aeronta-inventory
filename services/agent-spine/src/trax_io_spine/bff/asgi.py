"""ASGI entrypoint for deploying the Planner-UI BFF.

Seeds a store for one tenant and exposes the FastAPI app for uvicorn. Deploy-only
— keeps `create_planner_app` pure. Config via env:
  PLANNER_TENANT       tenant id to seed     (default: acme)
  DATABASE_URL         Postgres connection string. When set, boots a `PgPlannerStore`
                       against it instead of any in-memory store below: builds a pool,
                       resolves PLANNER_TENANT's slug to a tenant uuid (the tenant must
                       already be seeded — run `trax-io-pg-seed` first), and serves off
                       Postgres. Highest precedence — none of the paths below run.
                       Any role with execute on public.resolve_tenant_slug works (it's
                       SECURITY DEFINER — see migration 0006); use trax_app in production.
                       Once resolved, PgPlannerStore still SET LOCALs per-request tenant
                       claims via tenant_conn for every subsequent query. Also builds a
                       TokenVerifier from AUTH_JWKS_URL/AUTH_JWT_SECRET (bff/auth.py) and
                       passes the resolved tenant uuid as `tenant_uuids` so the JWT
                       middleware can enforce the slug<->tenant_id match. Fails closed:
                       with no verifier configured (no AUTH_JWKS_URL/AUTH_JWT_SECRET),
                       boot raises RuntimeError before any DB connection is attempted —
                       set AUTH_DEV_MODE=1 to explicitly opt into unauthenticated local
                       dev against a real Postgres instead.
  AUTH_DEV_MODE        "1" to allow DATABASE_URL boot with no TokenVerifier configured
                       (dev-trusted path-param mode against real Postgres). Ignored
                       unless DATABASE_URL is set; has no effect on the other boot paths,
                       which are always dev-trusted regardless of this flag.
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
        from trax_io_spine.bff.auth import build_verifier_from_env
        from trax_io_spine.bff.billing import billing_summary
        from trax_io_spine.pg.db import make_pool, tenant_conn
        from trax_io_spine.pg.members import HttpxAdminApi, MembershipStore
        from trax_io_spine.pg.store import PgPlannerStore
        from trax_io_spine.pg.uploads import HttpxSignedUrlMinter, IngestJobStore

        verifier = build_verifier_from_env()
        if verifier is None and os.environ.get("AUTH_DEV_MODE") != "1":
            raise RuntimeError(
                "DATABASE_URL is set but no AUTH_JWKS_URL/AUTH_JWT_SECRET configured — "
                "refusing to serve multi-tenant data unauthenticated (set AUTH_DEV_MODE=1 "
                "to override for local dev)"
            )

        pool = make_pool(database_url)
        with pool.connection() as _conn:
            row = _conn.execute(
                "select public.resolve_tenant_slug(%s)", (tenant,)
            ).fetchone()
        tenant_uuid = str(row[0]) if row and row[0] is not None else None
        if tenant_uuid is None:
            raise RuntimeError(
                f"DATABASE_URL set but tenant {tenant!r} not found — run trax-io-pg-seed first"
            )
        store = PgPlannerStore(pool, tenant_slug=tenant, tenant_uuid=tenant_uuid)
        # Members management NEVER runs in dev-trusted mode — members_stores is
        # always populated (RLS still gates every write on the caller's verified
        # role), but admin_api needs live Supabase project credentials. Without
        # them, listing still works; invite 502s "identity provider error"
        # (see bff/members_routes.py) rather than silently no-op'ing.
        supabase_url = os.environ.get("SUPABASE_URL", "").strip()
        supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
        admin_api = (
            HttpxAdminApi(supabase_url, supabase_service_key)
            if supabase_url and supabase_service_key
            else None
        )
        members_stores = {tenant: MembershipStore(pool, tenant_uuid=tenant_uuid)}
        # C3 Task 5: same supabase_url/supabase_service_key pair used for
        # admin_api above also mints signed Storage upload URLs — absent
        # either, upload_minter stays None and the uploads route 503s (ingest
        # create/poll/history are unaffected, they only need the pool).
        upload_minter = (
            HttpxSignedUrlMinter(supabase_url, supabase_service_key)
            if supabase_url and supabase_service_key
            else None
        )
        ingest_stores = {tenant: IngestJobStore(pool, tenant_uuid=tenant_uuid)}

        # C4: gate writes on a real, live-read subscription status (billing
        # migration 0010's `tenants.subscription_status`). A single indexed
        # read per write request — no per-uuid caching, so a status change
        # (e.g. a lapsed card) takes effect on the very next write.
        def _sub_status_for(t_uuid: str) -> str | None:
            with pool.connection() as c:
                row = c.execute(
                    "select subscription_status::text from tenants where id = %s::uuid",
                    (t_uuid,),
                ).fetchone()
            return row[0] if row else None

        # C4 Task 8: billing status + usage read. `tenants`/`part_keys` are both
        # RLS-protected (`tenants_select`/`part_keys_select`, keyed on
        # current_tenant_id()) and trax_app has NOBYPASSRLS in production — a
        # bare pool.connection() with no claims GUC set sees zero rows (NOT an
        # error; billing_summary would misreport as "unknown tenant"). Unlike
        # _sub_status_for above (which reads the same table the same bare way),
        # use tenant_conn here so the transaction carries the tenant's claims
        # and the RLS policies actually resolve this tenant's row.
        def _billing_reader(t_uuid: str):
            with tenant_conn(pool, tenant_uuid=t_uuid) as c:
                return billing_summary(c, t_uuid)

        return create_planner_app(
            {tenant: store},
            verifier=verifier,
            tenant_uuids={tenant: tenant_uuid},
            admin_api=admin_api,
            members_stores=members_stores,
            upload_minter=upload_minter,
            ingest_stores=ingest_stores,
            subscription_status_for=_sub_status_for,
            billing_reader=_billing_reader,
        )

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
