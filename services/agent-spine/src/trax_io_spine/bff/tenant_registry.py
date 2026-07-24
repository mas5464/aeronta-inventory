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
"""
from __future__ import annotations

import threading

from trax_io_spine.pg.members import MembershipStore
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

    def any_members_store(self) -> MembershipStore | None:
        """activate-tenant needs *a* store: tenant_preferences RLS gates on the
        JWT `sub` only, so any tenant's store works. Returns a cached one, or
        builds against any existing tenant."""
        with self._lock:
            if self._members:
                return next(iter(self._members.values()))
        with self._pool.connection() as conn:
            row = conn.execute("select slug from tenants limit 1").fetchone()
        return self.members_store_for(row[0]) if row else None

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
