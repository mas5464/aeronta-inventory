"""On-demand tenant resolution for the multi-tenant BFF (C5).

Replaces the single-`PLANNER_TENANT`-at-boot model: any tenant that exists in
Postgres is servable, resolved on first use and cached.

Two deliberate choices:

* **Positive resolutions are cached; misses are NOT.** A tenant created
  seconds ago must be reachable immediately, so a miss costs one extra
  round-trip rather than being shadowed by a stale negative entry.
* **No eviction.** Cached entries are a slug, a uuid, and store objects that
  hold only a pool reference (`PgPlannerStore.with_principal`'s docstring:
  "Construction is cheap"). Unbounded is correct at current tenant counts;
  eviction is out of scope (YAGNI).

Resolution goes through `public.resolve_tenant_slug` (SECURITY DEFINER,
migration 0006). It has to: `tenants` is RLS-scoped on the claims GUC, and
without a uuid we cannot open a `tenant_conn` to set that claim.

`members_store_for_uuid` is the one exception to all of the above: its
caller (`activate_tenant`) already holds a verified tenant uuid straight off
the caller's own JWT claims, so there is nothing to resolve and no reason to
query Postgres at all — see its docstring.
"""
from __future__ import annotations

import threading

from trax_io_spine.pg.members import MembershipStore
from trax_io_spine.pg.planning import PgPlanningRunStore
from trax_io_spine.pg.replay import PgReplayRunStore
from trax_io_spine.pg.store import PgPlannerStore
from trax_io_spine.pg.uploads import IngestJobStore


class TenantRegistry:
    def __init__(self, pool, *, open_orders=None) -> None:
        self._pool = pool
        self._open_orders = open_orders
        self._lock = threading.Lock()
        self._uuids: dict[str, str] = {}
        self._stores: dict[str, PgPlannerStore] = {}
        self._members: dict[str, MembershipStore] = {}
        self._members_by_uuid: dict[str, MembershipStore] = {}
        self._ingest: dict[str, IngestJobStore] = {}

    def uuid_for_slug(self, slug: str) -> str | None:
        cached = self._uuids.get(slug)
        if cached is not None:
            return cached
        with self._pool.connection() as conn:
            row = conn.execute(
                "select public.resolve_tenant_slug(%s)", (slug,)
            ).fetchone()
        uuid = str(row[0]) if row and row[0] is not None else None
        if uuid is not None:  # never cache a miss
            with self._lock:
                self._uuids[slug] = uuid
        return uuid

    def store_for(self, slug: str) -> PgPlannerStore | None:
        return self._resolve(slug, self._stores, self._build_store)

    def members_store_for(self, slug: str) -> MembershipStore | None:
        return self._resolve(slug, self._members, self._build_members)

    def ingest_store_for(self, slug: str) -> IngestJobStore | None:
        return self._resolve(slug, self._ingest, self._build_ingest)

    def planning_store_for(
        self,
        slug: str,
        *,
        principal: str,
        role: str,
    ) -> PgPlanningRunStore | None:
        """Build an identity-bound planning store after resolving the slug.

        Planning stores carry the verified caller identity used by
        ``tenant_conn`` for RLS and audit attribution. They are deliberately
        not cached: construction only binds a pool plus scalar identity, and
        caching would risk reusing one caller's claims for another request.
        """

        uuid = self.uuid_for_slug(slug)
        if uuid is None:
            return None
        return PgPlanningRunStore(
            self._pool,
            tenant_slug=slug,
            tenant_uuid=uuid,
            principal=principal,
            role=role,
        )

    def replay_store_for(
        self,
        slug: str,
        *,
        principal: str,
        role: str,
    ) -> PgReplayRunStore | None:
        """Build a fresh caller-bound historical replay store.

        Like planning runs, replay reads and submissions carry the verified
        identity into ``tenant_conn``. These lightweight bindings must not be
        cached across callers.
        """

        uuid = self.uuid_for_slug(slug)
        if uuid is None:
            return None
        return PgReplayRunStore(
            self._pool,
            tenant_slug=slug,
            tenant_uuid=uuid,
            principal=principal,
            role=role,
        )

    def members_store_for_uuid(self, tenant_uuid: str) -> MembershipStore:
        """Build (and cache) a `MembershipStore` bound to a tenant uuid the
        CALLER already holds — e.g. `activate_tenant`'s own verified
        `claims["tenant_id"]`. No slug lookup and no database round-trip at
        all: unlike `members_store_for`, this cannot fail to find a tenant,
        so it returns a store, never `None`.

        Safe for ANY tenant uuid the caller legitimately holds (not
        necessarily their currently-active one): `tenant_preferences` RLS
        gates writes on the JWT `sub` only (see
        `MembershipStore.set_preference`), not on the connection's tenant
        scope — so the store just needs to be bound to a real tenant, which
        the caller's own uuid, straight off their verified claims, always is.

        This replaces the former `any_members_store()`, whose cold-cache
        fallback ran `select slug from tenants limit 1` on a BARE pool
        connection to discover "any" tenant. `tenants` is RLS-protected
        (`tenants_select`, keyed on `current_tenant_id()`) and the BFF's
        `trax_app` role is NOBYPASSRLS, so that query returned zero rows in
        production no matter how many tenants existed — `any_members_store`
        would return `None` and its caller would 503 on every cold start.
        Once the caller already has a uuid in hand, "discover any tenant" was
        never the right question to ask Postgres in the first place.

        Cached in a uuid-keyed dict, kept separate from `_members` (which is
        slug-keyed) — the two keyspaces must never share one dict. Same
        lock + setdefault pattern as `_resolve`: concurrent callers are safe,
        at worst building one redundant (cheap) `MembershipStore`.
        """
        cached = self._members_by_uuid.get(tenant_uuid)
        if cached is not None:
            return cached
        built = MembershipStore(self._pool, tenant_uuid=tenant_uuid)
        with self._lock:
            return self._members_by_uuid.setdefault(tenant_uuid, built)

    def known_slugs(self) -> list[str]:
        """Slugs resolved so far — a cache-warmth signal for /healthz, NOT the
        set of servable tenants (which is every tenant in the database)."""
        with self._lock:
            return sorted(self._uuids)

    def _resolve(self, slug, cache, build):
        cached = cache.get(slug)
        if cached is not None:
            return cached
        uuid = self.uuid_for_slug(slug)
        if uuid is None:
            return None
        built = build(slug, uuid)
        with self._lock:
            return cache.setdefault(slug, built)

    def _build_store(self, slug: str, uuid: str) -> PgPlannerStore:
        return PgPlannerStore(
            self._pool, tenant_slug=slug, tenant_uuid=uuid, open_orders=self._open_orders
        )

    def _build_members(self, slug: str, uuid: str) -> MembershipStore:
        return MembershipStore(self._pool, tenant_uuid=uuid)

    def _build_ingest(self, slug: str, uuid: str) -> IngestJobStore:
        return IngestJobStore(self._pool, tenant_uuid=uuid)
