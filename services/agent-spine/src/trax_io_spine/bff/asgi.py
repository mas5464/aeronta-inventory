"""ASGI entrypoint for deploying the Planner-UI BFF.

Seeds a store for one tenant and exposes the FastAPI app for uvicorn. Deploy-only
— keeps `create_planner_app` pure. Config via env:
  PLANNER_TENANT       tenant slug to PRE-WARM at boot (default: acme). In
                       DATABASE_URL mode this is a HINT, not a requirement (C5
                       Task 6): a `TenantRegistry` resolves any tenant in the
                       database on demand, so an unresolvable (or entirely
                       unset) value just means boot logs a warning and skips
                       the pre-warm — it never raises. Ignored by every other
                       boot path below, where it is still required exactly as
                       before.
  DATABASE_URL         Postgres connection string. When set, boots a `PgPlannerStore`
                       against it instead of any in-memory store below: builds a pool
                       and a `TenantRegistry` over it, optionally pre-warms
                       PLANNER_TENANT's slug (see above), and serves off Postgres —
                       ANY tenant that exists in the database is servable, resolved
                       per-request via the registry, not just the pre-warmed one.
                       Highest precedence — none of the paths below run.
                       Slug resolution goes through public.resolve_tenant_slug (any
                       role with execute works — it's SECURITY DEFINER, migration
                       0006; use trax_app in production). Once resolved, PgPlannerStore
                       still SET LOCALs per-request tenant claims via tenant_conn for
                       every subsequent query. Also builds a TokenVerifier from
                       AUTH_JWKS_URL/AUTH_JWT_SECRET (bff/auth.py) and passes the SAME
                       registry instance as both `registry` (store/members/ingest
                       resolution) and `tenant_uuid_for=registry.uuid_for_slug` (the
                       JWT middleware's slug<->tenant_id match) — one resolver, so the
                       two can never disagree about which slug maps to which tenant.
                       Fails closed: with no verifier configured (no AUTH_JWKS_URL/
                       AUTH_JWT_SECRET), boot raises RuntimeError before any DB
                       connection is attempted — set AUTH_DEV_MODE=1 to explicitly
                       opt into unauthenticated local dev against a real Postgres
                       instead.
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

import logging
import os
from datetime import UTC, datetime

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.store import PlannerStore

log = logging.getLogger("trax_io_spine.bff.asgi")


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
        from trax_io_spine.bff.tenant_registry import TenantRegistry
        from trax_io_spine.bff.whoami import build_whoami_response, tenants_for
        from trax_io_spine.pg.db import make_pool, tenant_conn
        from trax_io_spine.pg.members import HttpxAdminApi
        from trax_io_spine.pg.uploads import HttpxSignedUrlMinter

        verifier = build_verifier_from_env()
        if verifier is None and os.environ.get("AUTH_DEV_MODE") != "1":
            raise RuntimeError(
                "DATABASE_URL is set but no AUTH_JWKS_URL/AUTH_JWT_SECRET configured — "
                "refusing to serve multi-tenant data unauthenticated (set AUTH_DEV_MODE=1 "
                "to override for local dev)"
            )

        pool = make_pool(database_url)
        # C5 Task 6: the ONE resolver instance shared by the store layer below
        # (registry=) AND the JWT middleware (tenant_uuid_for=registry.uuid_for_slug,
        # passed at the bottom of this branch) — never two separate instances,
        # so the two can never disagree about which slug maps to which tenant.
        registry = TenantRegistry(pool)

        # PLANNER_TENANT is a PRE-WARM HINT now, not a requirement: any tenant
        # in the database is servable through `registry` regardless of whether
        # this resolves. A fresh deployment may have no tenants seeded at all
        # yet, so an unresolvable hint logs a warning and boots anyway — the
        # old behavior (raise RuntimeError) made a brand-new deployment's very
        # first boot fail before a single tenant had ever signed up.
        tenant_uuid = registry.uuid_for_slug(tenant)
        if tenant_uuid is None:
            log.warning(
                "PLANNER_TENANT=%r not resolvable at boot (fresh deployment, or "
                "not yet seeded) — booting with no pre-warmed tenant; every real "
                "tenant is still servable on demand via the registry",
                tenant,
            )

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
        # C3 Task 5: same supabase_url/supabase_service_key pair used for
        # admin_api above also mints signed Storage upload URLs — absent
        # either, upload_minter stays None and the uploads route 503s (ingest
        # create/poll/history are unaffected, they only need the pool).
        upload_minter = (
            HttpxSignedUrlMinter(supabase_url, supabase_service_key)
            if supabase_url and supabase_service_key
            else None
        )

        # Pre-warm the four static dicts ONLY when PLANNER_TENANT actually
        # resolved. An unresolved (or unset) hint leaves all four empty —
        # exactly as if PLANNER_TENANT had never been configured at all —
        # relying entirely on `registry`/`tenant_uuid_for` below to resolve
        # every tenant (including this one) on first request. Fix round 1,
        # Fix 3: all three object-holding dicts now go through
        # `registry.store_for`/`.members_store_for`/`.ingest_store_for` —
        # never a fresh `PgPlannerStore(...)`/`MembershipStore(...)`/
        # `IngestJobStore(...)` construction here (members_stores/
        # ingest_stores used to build those directly, a second, independent
        # construction of the same kind of object `registry` would hand a
        # not-pre-warmed caller for this same tenant later). Routing all
        # three through `registry` populates the SAME cache it serves later
        # requests from, so pre-warming never creates a second, redundant
        # object for this one tenant — and each call below is a cheap cache
        # hit, not a fresh Postgres round-trip: `tenant_uuid`'s own
        # resolution just above already populated `registry`'s internal uuid
        # cache for `tenant`.
        if tenant_uuid is not None:
            stores = {tenant: registry.store_for(tenant)}
            tenant_uuids = {tenant: tenant_uuid}
            members_stores = {tenant: registry.members_store_for(tenant)}
            ingest_stores = {tenant: registry.ingest_store_for(tenant)}
        else:
            stores = {}
            tenant_uuids = {}
            members_stores = {}
            ingest_stores = {}

        # C4: gate writes on a real, live-read subscription status (billing
        # migration 0010's `tenants.subscription_status`). A single indexed
        # read per write request — no per-uuid caching, so a status change
        # (e.g. a lapsed card) takes effect on the very next write.
        # `tenants` is RLS-protected (`tenants_select`, keyed on
        # current_tenant_id()) and trax_app has NOBYPASSRLS in production —
        # a bare pool.connection() with no claims GUC set sees zero rows (NOT
        # an error; this would silently 402 every write for every tenant).
        # Use tenant_conn (same pattern as _billing_reader below) so the
        # transaction carries the tenant's claims and RLS resolves the row.
        def _sub_status_for(t_uuid: str) -> str | None:
            with tenant_conn(pool, tenant_uuid=t_uuid) as c:
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

        # C5 Task 5: GET /v1/auth/whoami. Unlike every reader above, this one
        # is NOT scoped to this boot's single `tenant`/`tenant_uuid` — it
        # answers "which tenants does THIS caller belong to", across every
        # tenant in the database, via `tenants_for_current_user()` (SECURITY
        # DEFINER, migration 20260724000013). That function depends entirely
        # on the `request.jwt.claims` GUC's `sub` — same tenant_conn
        # requirement as every reader above (a bare pool.connection() would
        # silently return zero memberships for every caller, not an error;
        # see whoami.py's `tenants_for` docstring and
        # tests/pg/test_c5_whoami_reader.py for the regression proof).
        # `active_tenant_uuid` (the caller's OWN verified claim) is present on
        # every reachable HTTP call (AuthMiddleware 401s first otherwise), so
        # it wins this fallback in practice; `tenant_uuid` (this boot's own
        # pre-warm, C5 Task 6: now possibly None when PLANNER_TENANT didn't
        # resolve) is a best-effort filler for tenant_conn's required
        # parameter in the one case it wouldn't — the function itself ignores
        # this value either way, so a None filler is inert, not a crash.
        def _whoami_reader(sub: str, active_tenant_uuid: str | None):
            with tenant_conn(pool, tenant_uuid=active_tenant_uuid or tenant_uuid, sub=sub) as c:
                tenants = tenants_for(c)
            return build_whoami_response(sub, active_tenant_uuid, tenants)

        return create_planner_app(
            stores,
            verifier=verifier,
            tenant_uuids=tenant_uuids,
            admin_api=admin_api,
            members_stores=members_stores,
            upload_minter=upload_minter,
            ingest_stores=ingest_stores,
            subscription_status_for=_sub_status_for,
            billing_reader=_billing_reader,
            whoami_reader=_whoami_reader,
            registry=registry,
            tenant_uuid_for=registry.uuid_for_slug,
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
