# C5 — Multi-Tenant Serving + Scheduled Recompute Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the BFF serve *any* authenticated tenant on demand (so a brand-new self-serve signup reaches the product with zero manual activation), and add a nightly per-tenant recompute driven by pg_cron.

**Architecture:** A `TenantRegistry` resolves slug↔uuid against Postgres on demand and lazily caches per-tenant stores (construction is free — `PgPlannerStore` holds only a pool reference). `AuthMiddleware` resolves through **that same registry** so the tenant-match assertion can never diverge from what the store layer serves. `GET /v1/auth/whoami` replaces the build-time `VITE_TENANT_SLUGS` map. Separately, a pg_cron job calls a SQL function that enqueues `jobs(kind='recompute')` rows, which the existing worker drains by replaying each tenant's last ingest payload under a preserve-mode seed.

**Tech Stack:** Postgres/plpgsql + pg_cron (Supabase), FastAPI + psycopg (BFF), React 18 + Vite + TanStack Query (`apps/web`), existing jobs worker.

**Spec:** [docs/superpowers/specs/2026-07-24-c5-multi-tenant-serving-design.md](../specs/2026-07-24-c5-multi-tenant-serving-design.md)

## Global Constraints

- **THE load-bearing security rule:** the middleware's tenant-uuid resolution and the store layer's tenant resolution **MUST come from the same resolver**. Today `auth.py` reads a static `tenant_uuids` dict and *skips* the claim match when the slug is absent (`if expected is not None and ...`) — safe only because an unconfigured tenant has no store. Once stores resolve dynamically, that skip becomes a cross-tenant read (tenant-A token → tenant-B data). **After this plan, an unresolvable slug must REJECT, never fall through.**
- The **JWT `tenant_id` claim stays authoritative**; the URL slug is addressing only. Dynamic resolution changes *reachability*, never *authorization*. RLS remains the hard boundary.
- **Unresolvable slug → 403** (not 404): no existence oracle for org slugs. *Note: this changes today's behavior* (today it falls through to a 404 from `_store`). Verified: no existing test asserts a 404 for an unknown tenant slug over HTTP, so nothing regresses.
- **Never delete `writeback_ledger` or `kill_switches` in a recompute** (append-only audit + a safety control). `decisions` is already outside `_SEEDED_TABLES` and stays preserved.
- **Upload-ingest behavior must stay byte-for-byte unchanged** — it passes no `preserve`, so it keeps full-replace semantics.
- Recompute eligibility: prior successful ingest **AND** `subscription_status ∈ {trialing, active, past_due}` **AND** no `ingest`/`recompute` already `queued|running`.
- **pg_cron registration is a rollout step, never a migration** — `create extension pg_cron` fails on the throwaway test container and would break the pg suite. Verified live: pg_cron 1.6.4 available, `installed_version = None`.
- New SQL functions are `SECURITY DEFINER` but strictly caller-scoped, with `revoke execute … from public` before their grants.
- Migrations are plain SQL in `supabase/migrations/`, timestamp-named after `20260723000012`, RLS in the same migration, applied by both `supabase db push` and the pg test harness.
- **Test commands:** pg/bff `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg tests/bff` (Docker/testcontainers); web `cd apps/web && npm test && npm run build && npm run lint`.
- **Registry caching:** cache *positive* slug→uuid resolutions; **never cache a miss** (a tenant created seconds ago must be reachable immediately). Unbounded dict is correct at current scale — eviction is explicitly out of scope (YAGNI).

---

## File Structure

**New — migrations (`supabase/migrations/`):**
- `20260724000013_tenants_for_current_user.sql` — the caller-scoped membership-listing function.
- `20260724000014_enqueue_due_recomputes.sql` — eligibility + enqueue function.

**New — BFF:**
- `services/agent-spine/src/trax_io_spine/bff/tenant_registry.py` — `TenantRegistry`: slug↔uuid resolution + lazy per-tenant store construction. One responsibility, no HTTP.
- `services/agent-spine/src/trax_io_spine/bff/whoami.py` — the `/v1/auth/whoami` router (kept out of `app.py`, which is already large).

**Modified — BFF:**
- `bff/auth.py` — `AuthMiddleware` takes `tenant_uuid_for` resolver; unresolvable → 403.
- `bff/app.py` — `_store`/members/ingest resolve via the registry; `create_planner_app(..., registry=None)`; mount whoami router.
- `bff/asgi.py` — build the registry in `DATABASE_URL` mode; `PLANNER_TENANT` becomes optional (pre-warm only).
- `bff/members_routes.py` — `activate_tenant`'s `next(iter(stores.values()))` → registry.

**Modified — recompute:**
- `pg/seed.py` — `seed_store(..., preserve: frozenset[str] = frozenset())`.
- `pg/ingest.py` — `run_ingest(..., preserve=frozenset())` threaded to `seed_store`.
- `pg/worker.py` — `HANDLERS["recompute"]`.

**Modified — frontend:**
- `apps/web/src/lib/api/whoami.ts` (new) — `getWhoami()`.
- `apps/web/src/lib/auth/useAuth.tsx` — consume whoami; `apps/web/src/lib/auth/supabase.ts` — delete `tenantSlugByUuid`; `apps/web/src/components/TenantSwitcher.tsx` — use the whoami list.

**New/modified — rollout:** `deploy/C5_ROLLOUT.md`; `deploy/C4_ROLLOUT.md` (delete the manual-activation limitation); `ROADMAP.md`, `TASKS.md`, `CLAUDE.md`.

---

## GROUP 1 — Migrations

### Task 1: Migration 0013 — `tenants_for_current_user()`

**Files:**
- Create: `supabase/migrations/20260724000013_tenants_for_current_user.sql`
- Test: `services/agent-spine/tests/pg/test_c5_tenants_for_user.py`

**Interfaces:**
- Produces: `public.tenants_for_current_user() returns table (tenant_uuid uuid, slug text, name text, role text)` — rows for the caller (`auth.jwt()->>'sub'`) only. Granted to `authenticated`, `trax_app`.

- [ ] **Step 1: Write the failing test**

```python
# services/agent-spine/tests/pg/test_c5_tenants_for_user.py
"""tenants_for_current_user(): returns the CALLER's memberships only.

Uses pg_admin_conn (autocommit superuser). Claims are transaction-local
(set_config(..., true)), so every claim-scoped call is wrapped in an explicit
transaction — see tests/pg/test_c4_stripe_mirror.py for the same pattern.
"""
import uuid


def _rows_for(conn, user_id: str):
    with conn.transaction():
        conn.execute("set role authenticated")
        conn.execute(
            "select set_config('request.jwt.claims', %s, true)",
            (f'{{"sub":"{user_id}"}}',),
        )
        rows = conn.execute(
            "select slug, role from public.tenants_for_current_user() order by slug"
        ).fetchall()
        conn.execute("reset role")
    return rows


def test_returns_only_callers_memberships(pg_admin_conn):
    u1, u2 = str(uuid.uuid4()), str(uuid.uuid4())
    a = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-a','A') returning id").fetchone()[0]
    b = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-b','B') returning id").fetchone()[0]
    pg_admin_conn.execute(
        "insert into memberships (user_id,tenant_id,role) values (%s,%s,'owner'),(%s,%s,'planner')",
        (u1, a, u1, b))
    pg_admin_conn.execute(
        "insert into memberships (user_id,tenant_id,role) values (%s,%s,'owner')", (u2, b))

    assert _rows_for(pg_admin_conn, u1) == [("c5-a", "owner"), ("c5-b", "planner")]
    # u2 sees ONLY its own membership — no leakage of u1's tenant-a row.
    assert _rows_for(pg_admin_conn, u2) == [("c5-b", "owner")]


def test_no_memberships_returns_empty(pg_admin_conn):
    assert _rows_for(pg_admin_conn, str(uuid.uuid4())) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_c5_tenants_for_user.py -v`
Expected: FAIL — `function public.tenants_for_current_user() does not exist`.

- [ ] **Step 3: Write the migration**

```sql
-- supabase/migrations/20260724000013_tenants_for_current_user.sql
-- C5: list the CALLER's tenant memberships.
--
-- Normal RLS cannot express this: `memberships` is scoped to
-- current_tenant_id(), so a user connected as tenant A cannot see their
-- tenant-B row. This is the same sanctioned SECURITY DEFINER exception as
-- C4's create_tenant_for_current_user — strictly caller-scoped, no arguments,
-- so it can only ever return the caller's own rows.
create function public.tenants_for_current_user()
returns table (tenant_uuid uuid, slug text, name text, role text)
language sql
stable
security definer
set search_path = public
as $$
  select t.id, t.slug, t.name, m.role
    from public.memberships m
    join public.tenants t on t.id = m.tenant_id
   where m.user_id = (auth.jwt()->>'sub')::uuid
   order by t.slug
$$;

revoke execute on function public.tenants_for_current_user() from public;
grant execute on function public.tenants_for_current_user() to authenticated, trax_app;
```

- [ ] **Step 4: Run test to verify it passes**

Run the same command. Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20260724000013_tenants_for_current_user.sql services/agent-spine/tests/pg/test_c5_tenants_for_user.py
git commit -m "feat(c5): migration 0013 — caller-scoped tenants_for_current_user()"
```

---

### Task 2: Migration 0014 — `enqueue_due_recomputes()`

**Files:**
- Create: `supabase/migrations/20260724000014_enqueue_due_recomputes.sql`
- Test: `services/agent-spine/tests/pg/test_c5_enqueue_recomputes.py`

**Interfaces:**
- Produces: `public.enqueue_due_recomputes() returns integer` — inserts one `jobs(kind='recompute')` row per eligible tenant, returns the count. Payload = the tenant's last successful ingest payload plus `"source":"recompute"`.
- Consumes: `jobs(tenant_id, kind, payload, status)` (migration 0006), `tenants.subscription_status` (0010).

- [ ] **Step 1: Write the failing test**

```python
# services/agent-spine/tests/pg/test_c5_enqueue_recomputes.py
"""enqueue_due_recomputes(): eligibility matrix + idempotency.

Runs as pg_admin_conn (superuser) — this function is invoked by pg_cron in
production, never through a request path, so there is no claim to set.
"""
import json


def _tenant(conn, slug, status):
    return conn.execute(
        "insert into tenants (slug,name,subscription_status) values (%s,%s,%s) returning id",
        (slug, slug, status)).fetchone()[0]


def _done_ingest(conn, tid, files=None):
    payload = json.dumps({"tenant_id": str(tid), "tenant_slug": "s",
                          "files": files or {"parts": "p/x/parts"}})
    conn.execute(
        "insert into jobs (tenant_id,kind,payload,status) values (%s,'ingest',%s::jsonb,'done')",
        (tid, payload))


def _queued(conn, tid):
    return conn.execute(
        "select payload from jobs where tenant_id=%s and kind='recompute'", (tid,)).fetchall()


def test_eligible_tenant_gets_one_row_with_copied_payload(pg_admin_conn):
    t = _tenant(pg_admin_conn, "c5-elig", "active")
    _done_ingest(pg_admin_conn, t, {"parts": "p/b1/parts", "stock": "p/b1/stock"})
    n = pg_admin_conn.execute("select public.enqueue_due_recomputes()").fetchone()[0]
    assert n >= 1
    rows = _queued(pg_admin_conn, t)
    assert len(rows) == 1
    payload = rows[0][0]
    assert payload["files"] == {"parts": "p/b1/parts", "stock": "p/b1/stock"}
    assert payload["source"] == "recompute"


def test_no_prior_ingest_is_skipped(pg_admin_conn):
    t = _tenant(pg_admin_conn, "c5-noingest", "active")
    pg_admin_conn.execute("select public.enqueue_due_recomputes()")
    assert _queued(pg_admin_conn, t) == []


def test_lapsed_subscription_is_skipped(pg_admin_conn):
    t = _tenant(pg_admin_conn, "c5-lapsed", "canceled")
    _done_ingest(pg_admin_conn, t)
    pg_admin_conn.execute("select public.enqueue_due_recomputes()")
    assert _queued(pg_admin_conn, t) == []


def test_already_running_is_skipped(pg_admin_conn):
    t = _tenant(pg_admin_conn, "c5-busy", "active")
    _done_ingest(pg_admin_conn, t)
    pg_admin_conn.execute(
        "insert into jobs (tenant_id,kind,payload,status) "
        "values (%s,'ingest','{}'::jsonb,'running')", (t,))
    pg_admin_conn.execute("select public.enqueue_due_recomputes()")
    assert _queued(pg_admin_conn, t) == []


def test_idempotent_second_call_enqueues_nothing_more(pg_admin_conn):
    t = _tenant(pg_admin_conn, "c5-idem", "trialing")
    _done_ingest(pg_admin_conn, t)
    pg_admin_conn.execute("select public.enqueue_due_recomputes()")
    pg_admin_conn.execute("select public.enqueue_due_recomputes()")
    assert len(_queued(pg_admin_conn, t)) == 1  # the queued row blocks a second enqueue
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_c5_enqueue_recomputes.py -v`
Expected: FAIL — `function public.enqueue_due_recomputes() does not exist`.

- [ ] **Step 3: Write the migration**

```sql
-- supabase/migrations/20260724000014_enqueue_due_recomputes.sql
-- C5: nightly per-tenant recompute enqueue.
--
-- Invoked by pg_cron (see deploy/C5_ROLLOUT.md) — NOT exposed through any HTTP
-- route, so no tenant can trigger another tenant's compute. The pg_cron
-- extension is deliberately NOT created here: it is unavailable on the
-- throwaway test container and would break the pg suite. This function is
-- pure SQL so the eligibility logic stays fully testable without it.
create function public.enqueue_due_recomputes()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer := 0;
begin
  with eligible as (
    select t.id as tenant_id,
           (select j.payload
              from public.jobs j
             where j.tenant_id = t.id and j.kind = 'ingest' and j.status = 'done'
             order by j.id desc
             limit 1) as last_payload
      from public.tenants t
     where t.subscription_status in ('trialing','active','past_due')
       -- must have something to replay
       and exists (select 1 from public.jobs j
                    where j.tenant_id = t.id and j.kind = 'ingest' and j.status = 'done')
       -- dedup: never stack work on a tenant that is already busy
       and not exists (select 1 from public.jobs j
                        where j.tenant_id = t.id
                          and j.kind in ('ingest','recompute')
                          and j.status in ('queued','running'))
  )
  insert into public.jobs (tenant_id, kind, payload, status)
  select tenant_id, 'recompute',
         last_payload || jsonb_build_object('source', 'recompute'),
         'queued'
    from eligible
   where last_payload is not null;

  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke execute on function public.enqueue_due_recomputes() from public;
-- Only the cron owner (postgres) and the worker role need this. Deliberately
-- NOT granted to authenticated/trax_app: no request path may trigger compute.
grant execute on function public.enqueue_due_recomputes() to trax_seed;
```

- [ ] **Step 4: Run test to verify it passes**

Run the same command. Expected: PASS (5 tests).

- [ ] **Step 5: Run the full pg suite (regression) + commit**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg -q`

```bash
git add supabase/migrations/20260724000014_enqueue_due_recomputes.sql services/agent-spine/tests/pg/test_c5_enqueue_recomputes.py
git commit -m "feat(c5): migration 0014 — enqueue_due_recomputes() eligibility + dedup"
```

---

## GROUP 2 — Tenant registry

### Task 3: `bff/tenant_registry.py`

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/bff/tenant_registry.py`
- Test: `services/agent-spine/tests/pg/test_c5_tenant_registry.py`

**Interfaces:**
- Produces:
  ```python
  class TenantRegistry:
      def __init__(self, pool, *, open_orders=None) -> None
      def uuid_for_slug(self, slug: str) -> str | None
      def store_for(self, slug: str) -> PgPlannerStore | None
      def members_store_for(self, slug: str) -> MembershipStore | None
      def ingest_store_for(self, slug: str) -> IngestJobStore | None
      def any_members_store(self) -> MembershipStore | None   # for activate_tenant
      def known_slugs(self) -> list[str]                       # for /healthz
  ```
- Consumes: `public.resolve_tenant_slug(text) -> uuid` (migration 0006, SECURITY DEFINER — the only option here: without a uuid we cannot open a `tenant_conn`); `PgPlannerStore(pool, tenant_slug=, tenant_uuid=)`, `MembershipStore(pool, tenant_uuid=)`, `IngestJobStore(pool, tenant_uuid=)`.

- [ ] **Step 1: Write the failing test**

```python
# services/agent-spine/tests/pg/test_c5_tenant_registry.py
"""TenantRegistry resolves real tenants on demand against real Postgres."""
from trax_io_spine.bff.tenant_registry import TenantRegistry


def test_resolves_and_caches_a_real_tenant(pg_pool, pg_admin_conn):
    tid = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-reg','Reg') returning id").fetchone()[0]
    reg = TenantRegistry(pg_pool)
    assert reg.uuid_for_slug("c5-reg") == str(tid)
    store = reg.store_for("c5-reg")
    assert store is not None and store.tenant_id == "c5-reg"
    # Same object on a second call — cached, not rebuilt.
    assert reg.store_for("c5-reg") is store


def test_unknown_slug_returns_none_and_is_not_cached(pg_pool, pg_admin_conn):
    reg = TenantRegistry(pg_pool)
    assert reg.uuid_for_slug("c5-later") is None
    assert reg.store_for("c5-later") is None
    # A tenant created AFTER the miss must be reachable immediately: misses
    # are deliberately never cached.
    pg_admin_conn.execute("insert into tenants (slug,name) values ('c5-later','Later')")
    assert reg.uuid_for_slug("c5-later") is not None
    assert reg.store_for("c5-later") is not None


def test_members_and_ingest_stores_resolve(pg_pool, pg_admin_conn):
    pg_admin_conn.execute("insert into tenants (slug,name) values ('c5-mi','MI')")
    reg = TenantRegistry(pg_pool)
    assert reg.members_store_for("c5-mi") is not None
    assert reg.ingest_store_for("c5-mi") is not None
    assert reg.any_members_store() is not None
    assert "c5-mi" in reg.known_slugs()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_c5_tenant_registry.py -v`
Expected: FAIL — `No module named 'trax_io_spine.bff.tenant_registry'`.

- [ ] **Step 3: Implement**

```python
# services/agent-spine/src/trax_io_spine/bff/tenant_registry.py
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
```

If `pg_pool` (a `trax_app`-role pool fixture) does not exist in `tests/pg/conftest.py`, use the fixture the existing C4 tests use for role-scoped pools (see `tests/pg/test_c4_billing_read_pg.py`) — do not invent a new one.

- [ ] **Step 4: Run test to verify it passes**

Run the same command. Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/bff/tenant_registry.py services/agent-spine/tests/pg/test_c5_tenant_registry.py
git commit -m "feat(c5): TenantRegistry — on-demand slug/uuid resolution + lazy store cache"
```

---

## GROUP 3 — BFF integration

### Task 4: `AuthMiddleware` dynamic resolution (the security-critical task)

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/auth.py` (the `is_tenant_scoped` block, ~lines 122-127)
- Test: `services/agent-spine/tests/bff/test_c5_dynamic_tenant_match.py`

**Interfaces:**
- Consumes: `AuthMiddleware(app, verifier, tenant_uuids=None, subscription_status_for=None)` (existing).
- Produces: `AuthMiddleware(..., tenant_uuid_for=None)` where `tenant_uuid_for: Callable[[str], str | None]`. Resolution order: `tenant_uuid_for(slug)` when provided, else the static `tenant_uuids` dict. **An unresolvable slug → 403.** The role floor and the C4 402 gate keep their current position and semantics.

> **Why this task matters most:** today `expected is None` *skips* the tenant-match assertion. That is safe only because an unconfigured tenant has no store. Once Task 6 makes stores resolve dynamically, a skipped match means a tenant-A token can read tenant-B data. This task closes that before Task 6 opens it.

- [ ] **Step 1: Write the failing test**

```python
# services/agent-spine/tests/bff/test_c5_dynamic_tenant_match.py
"""Dynamic tenant resolution must not weaken the tenant-match assertion."""
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from fastapi.testclient import TestClient
from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.bff.store import PlannerStore

SECRET = "unit-test-secret-0123456789abcdef"
A_UUID = "11111111-1111-1111-1111-111111111111"
B_UUID = "22222222-2222-2222-2222-222222222222"
_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


class _V:
    def __init__(self):
        self._v = HsVerifier(SECRET)

    def verify(self, t):
        return self._v.verify(t)


def _tok(tenant_uuid: str, role: str = "planner") -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": "u1", "aud": "authenticated", "iat": now, "exp": now + timedelta(minutes=5),
         "tenant_id": tenant_uuid, "tenant_role": role},
        SECRET, algorithm="HS256")


def _client():
    """Two tenants resolvable dynamically; only tenant-a has a store configured
    (mirrors Task 6's registry, where any real slug resolves)."""
    store = PlannerStore.from_extract(
        tenant_id="tenant-a", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC))
    resolver = {"tenant-a": A_UUID, "tenant-b": B_UUID}.get
    app = create_planner_app(
        {"tenant-a": store}, verifier=_V(), tenant_uuid_for=resolver)
    return TestClient(app)


def test_matching_claim_is_served():
    r = _client().get("/v1/tenants/tenant-a/recommendations",
                      headers={"Authorization": f"Bearer {_tok(A_UUID)}"})
    assert r.status_code == 200


def test_cross_tenant_token_is_rejected_not_served():
    """THE regression guard: a tenant-B token addressing tenant-a must 403.
    Before C5 the match was skipped whenever the slug wasn't in the static
    dict, which would serve tenant-a's data to a tenant-b caller."""
    r = _client().get("/v1/tenants/tenant-a/recommendations",
                      headers={"Authorization": f"Bearer {_tok(B_UUID)}"})
    assert r.status_code == 403


def test_unresolvable_slug_is_403_not_a_fallthrough():
    r = _client().get("/v1/tenants/does-not-exist/recommendations",
                      headers={"Authorization": f"Bearer {_tok(A_UUID)}"})
    assert r.status_code == 403


def test_static_dict_path_still_works_without_resolver():
    store = PlannerStore.from_extract(
        tenant_id="tenant-a", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC))
    app = create_planner_app({"tenant-a": store}, verifier=_V(),
                             tenant_uuids={"tenant-a": A_UUID})
    r = TestClient(app).get("/v1/tenants/tenant-a/recommendations",
                            headers={"Authorization": f"Bearer {_tok(A_UUID)}"})
    assert r.status_code == 200


def test_resolver_failure_is_503_not_a_raw_500():
    """A DB blip during resolution must fail closed with a clean, retryable
    503 — never an unhandled exception, and never a fallthrough that would
    serve unverified data. Same posture as the C4 subscription gate."""
    def _boom(_slug):
        raise RuntimeError("pool exhausted")

    store = PlannerStore.from_extract(
        tenant_id="tenant-a", extract_dir=str(_SAMPLE), now=datetime(2026, 4, 1, tzinfo=UTC))
    app = create_planner_app({"tenant-a": store}, verifier=_V(), tenant_uuid_for=_boom)
    r = TestClient(app).get("/v1/tenants/tenant-a/recommendations",
                            headers={"Authorization": f"Bearer {_tok(A_UUID)}"})
    assert r.status_code == 503
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr pytest tests/bff/test_c5_dynamic_tenant_match.py -v`
Expected: FAIL — `create_planner_app() got an unexpected keyword argument 'tenant_uuid_for'`.

- [ ] **Step 3: Implement**

In `auth.py`, add the parameter and replace the resolution + match block:

```python
# __init__ signature: add tenant_uuid_for=None, and store it
self.tenant_uuid_for = tenant_uuid_for   # Callable[[str], str | None] | None

# inside __call__, replacing `expected = self.tenant_uuids.get(slug)` and the
# `if expected is not None and ...` match:
slug = path.split("/")[3]
if self.tenant_uuid_for is not None:
    try:
        # Resolution does a sync psycopg pool read — offload to a thread so
        # we don't block the event loop, exactly as the C4 gate below does.
        expected = await anyio.to_thread.run_sync(self.tenant_uuid_for, slug)
    except Exception:
        log.exception("tenant resolution failed for slug %s", slug)
        return await _reject(503, "tenant resolution unavailable")(scope, receive, send)
else:
    expected = self.tenant_uuids.get(slug)
# C5: an unresolvable slug must REJECT, never fall through. Pre-C5 this
# skipped the match (safe only because an unconfigured tenant had no store);
# with dynamically-resolved stores a skip would serve another tenant's data.
# 403 (not 404) keeps org slugs from becoming an existence oracle.
if expected is None or claims["tenant_id"] != expected:
    return await _reject(403, "tenant mismatch")(scope, receive, send)
```

`anyio` and `log` are already imported in this module (added by C4's write-gate). Failing **closed** on a resolution error is the point: a fallthrough here would serve a tenant whose identity was never verified.

Leave the role floor and the C4 402 gate exactly where they are. The gate's `expected is not None` guard is now always true for served requests, which is correct.

In `app.py`, thread the parameter: `create_planner_app(..., tenant_uuid_for=None)` → `app.add_middleware(AuthMiddleware, ..., tenant_uuid_for=tenant_uuid_for)`.

- [ ] **Step 4: Run test to verify it passes**

Run the same command. Expected: PASS (5 tests).
Then regression: `pytest tests/bff -q` — the existing auth suite must stay green (it uses the static-dict path).

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/bff/auth.py services/agent-spine/src/trax_io_spine/bff/app.py services/agent-spine/tests/bff/test_c5_dynamic_tenant_match.py
git commit -m "feat(c5): middleware resolves tenants dynamically; unresolvable slug now 403 (closes cross-tenant fallthrough)"
```

---

### Task 5: `GET /v1/auth/whoami`

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/bff/whoami.py`
- Modify: `services/agent-spine/src/trax_io_spine/bff/auth.py` (add the path to `_UNSCOPED_AUTHED_PATHS`), `bff/app.py` (mount the router)
- Test: `services/agent-spine/tests/bff/test_c5_whoami.py`

**Interfaces:**
- Produces: `GET /v1/auth/whoami` →
  ```json
  {"user_id": "…",
   "active": {"tenant_uuid": "…", "slug": "…", "name": "…", "role": "…"} | null,
   "tenants": [{"tenant_uuid": "…", "slug": "…", "name": "…", "role": "…"}]}
  ```
  Backed by `app.state.whoami_reader: Callable[[str, str | None], WhoamiResponse] | None` (args: `sub`, `active_tenant_uuid`). `503` when unconfigured.
- Consumes: `tenants_for_current_user()` (Task 1); `tenant_conn(pool, tenant_uuid, sub=…)` from `pg/db.py` (`tenant_claims` already carries `sub`).

- [ ] **Step 1: Write the failing test**

```python
# services/agent-spine/tests/bff/test_c5_whoami.py
from datetime import UTC, datetime, timedelta

import jwt
from fastapi.testclient import TestClient
from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.bff.whoami import TenantRef, WhoamiResponse

SECRET = "unit-test-secret-0123456789abcdef"
A_UUID = "11111111-1111-1111-1111-111111111111"


class _V:
    def __init__(self):
        self._v = HsVerifier(SECRET)

    def verify(self, t):
        return self._v.verify(t)


def _tok(tenant_uuid=A_UUID, role="owner"):
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": "user-1", "aud": "authenticated", "iat": now,
         "exp": now + timedelta(minutes=5), "tenant_id": tenant_uuid, "tenant_role": role},
        SECRET, algorithm="HS256")


def _client(reader):
    return TestClient(create_planner_app({}, verifier=_V(), whoami_reader=reader))


def test_returns_active_and_list():
    ref = TenantRef(tenant_uuid=A_UUID, slug="acme", name="Acme", role="owner")
    reader = lambda sub, active: WhoamiResponse(user_id=sub, active=ref, tenants=[ref])
    r = _client(reader).get("/v1/auth/whoami",
                            headers={"Authorization": f"Bearer {_tok()}"})
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "user-1"
    assert body["active"]["slug"] == "acme"
    assert [t["slug"] for t in body["tenants"]] == ["acme"]


def test_no_memberships_is_200_with_nulls():
    reader = lambda sub, active: WhoamiResponse(user_id=sub, active=None, tenants=[])
    r = _client(reader).get("/v1/auth/whoami",
                            headers={"Authorization": f"Bearer {_tok()}"})
    assert r.status_code == 200
    assert r.json()["active"] is None and r.json()["tenants"] == []


def test_unauthenticated_is_401():
    reader = lambda sub, active: WhoamiResponse(user_id=sub, active=None, tenants=[])
    assert _client(reader).get("/v1/auth/whoami").status_code == 401


def test_unconfigured_reader_is_503():
    assert TestClient(create_planner_app({}, verifier=_V())).get(
        "/v1/auth/whoami", headers={"Authorization": f"Bearer {_tok()}"}
    ).status_code == 503
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr pytest tests/bff/test_c5_whoami.py -v`
Expected: FAIL — `No module named 'trax_io_spine.bff.whoami'`.

- [ ] **Step 3: Implement**

```python
# services/agent-spine/src/trax_io_spine/bff/whoami.py
"""GET /v1/auth/whoami — who the caller is and which tenants they belong to.

Deliberately OUTSIDE /v1/tenants/{slug}/… (like activate-tenant): the caller
may have no active tenant at all (mid-signup), and there is no slug to match.
Replaces apps/web's build-time VITE_TENANT_SLUGS map.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class TenantRef(BaseModel):
    tenant_uuid: str
    slug: str
    name: str
    role: str


class WhoamiResponse(BaseModel):
    user_id: str
    active: TenantRef | None
    tenants: list[TenantRef]


@router.get("/v1/auth/whoami", response_model=WhoamiResponse)
def whoami(request: Request) -> WhoamiResponse:
    claims = getattr(request.state, "claims", None)
    if not claims or not claims.get("sub"):
        raise HTTPException(status_code=401, detail="missing or invalid token")
    reader = getattr(request.app.state, "whoami_reader", None)
    if reader is None:
        raise HTTPException(status_code=503, detail="whoami unavailable")
    return reader(claims["sub"], claims.get("tenant_id"))
```

In `auth.py` add `"/v1/auth/whoami"` to `_UNSCOPED_AUTHED_PATHS`. In `app.py`: `create_planner_app(..., whoami_reader=None)`, set `app.state.whoami_reader = whoami_reader`, and `app.include_router(whoami_router)`.

- [ ] **Step 4: Run test to verify it passes**

Run the same command. Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/bff/whoami.py services/agent-spine/src/trax_io_spine/bff/auth.py services/agent-spine/src/trax_io_spine/bff/app.py services/agent-spine/tests/bff/test_c5_whoami.py
git commit -m "feat(c5): GET /v1/auth/whoami (active tenant + membership list)"
```

---

### Task 6: Wire the registry through `app.py` / `asgi.py`

**Files:**
- Modify: `bff/app.py` (`_store` + members/ingest lookups), `bff/asgi.py` (build the registry, wire `tenant_uuid_for`/`whoami_reader`, make `PLANNER_TENANT` optional), `bff/members_routes.py` (`activate_tenant`), `bff/ingest_routes.py` (tenant lookups)
- Test: `services/agent-spine/tests/pg/test_c5_multi_tenant_serving.py`

**Interfaces:**
- Consumes: `TenantRegistry` (Task 3), `tenant_uuid_for` (Task 4), `whoami_reader` (Task 5).
- Produces: `create_planner_app(..., registry=None)`. When `registry` is set, `_store(slug)` / members / ingest resolve through it, falling back to the static dicts when it is `None` (dev/in-memory paths unchanged). `asgi.py` builds one `TenantRegistry` in `DATABASE_URL` mode and passes `registry`, `tenant_uuid_for=registry.uuid_for_slug`, `whoami_reader=_whoami_reader`.

- [ ] **Step 1: Write the failing test**

```python
# services/agent-spine/tests/pg/test_c5_multi_tenant_serving.py
"""One app instance serves two different tenants, with isolation intact."""
from datetime import UTC, datetime, timedelta

import jwt
from fastapi.testclient import TestClient
from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.bff.tenant_registry import TenantRegistry

SECRET = "unit-test-secret-0123456789abcdef"


class _V:
    def __init__(self):
        self._v = HsVerifier(SECRET)

    def verify(self, t):
        return self._v.verify(t)


def _tok(tenant_uuid, role="planner"):
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": "u1", "aud": "authenticated", "iat": now, "exp": now + timedelta(minutes=5),
         "tenant_id": str(tenant_uuid), "tenant_role": role},
        SECRET, algorithm="HS256")


def test_one_app_serves_two_tenants_and_blocks_crossover(pg_pool, pg_admin_conn):
    a = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-serve-a','A') returning id").fetchone()[0]
    b = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-serve-b','B') returning id").fetchone()[0]
    reg = TenantRegistry(pg_pool)
    app = create_planner_app({}, verifier=_V(), registry=reg,
                             tenant_uuid_for=reg.uuid_for_slug)
    c = TestClient(app)

    # Each tenant is served with its own token — neither was configured at boot.
    assert c.get("/v1/tenants/c5-serve-a/recommendations",
                 headers={"Authorization": f"Bearer {_tok(a)}"}).status_code == 200
    assert c.get("/v1/tenants/c5-serve-b/recommendations",
                 headers={"Authorization": f"Bearer {_tok(b)}"}).status_code == 200
    # Crossover is refused.
    assert c.get("/v1/tenants/c5-serve-b/recommendations",
                 headers={"Authorization": f"Bearer {_tok(a)}"}).status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_c5_multi_tenant_serving.py -v`
Expected: FAIL — `create_planner_app() got an unexpected keyword argument 'registry'`.

- [ ] **Step 3: Implement**

In `app.py`, `_store` resolves through the registry first:

```python
def _store(tenant_id: str, request: Request | None = None) -> PlannerStore:
    store = stores.get(tenant_id)
    if store is None and registry is not None:
        store = registry.store_for(tenant_id)
    if store is None:
        raise HTTPException(status_code=404, detail=f"unknown tenant {tenant_id}")
    ...  # principal attribution unchanged
```

Apply the same `registry`-fallback pattern where `app.state.members_stores` / `app.state.ingest_stores` are read (`members_routes.py`, `ingest_routes.py`) — store the registry on `app.state.registry` so those routers can reach it. `activate_tenant`'s `next(iter(stores.values()))` becomes: use a configured store if present, else `request.app.state.registry.any_members_store()`, else 503.

In `asgi.py` (`DATABASE_URL` branch): build `registry = TenantRegistry(pool)`; keep `PLANNER_TENANT` **optional** — if set and resolvable, pre-warm via `registry.store_for(tenant)`; if set and *not* resolvable, log a warning instead of raising (a fresh deployment may have no tenants yet). Add:

```python
from trax_io_spine.bff.whoami import TenantRef, WhoamiResponse
from trax_io_spine.pg.db import tenant_conn

def _whoami_reader(sub: str, active_uuid: str | None) -> WhoamiResponse:
    if active_uuid is None:
        return WhoamiResponse(user_id=sub, active=None, tenants=[])
    with tenant_conn(pool, active_uuid, sub=sub) as conn:
        rows = conn.execute(
            "select tenant_uuid::text, slug, name, role from public.tenants_for_current_user()"
        ).fetchall()
    refs = [TenantRef(tenant_uuid=r[0], slug=r[1], name=r[2], role=r[3]) for r in rows]
    active = next((r for r in refs if r.tenant_uuid == active_uuid), None)
    return WhoamiResponse(user_id=sub, active=active, tenants=refs)
```

Pass `registry=registry, tenant_uuid_for=registry.uuid_for_slug, whoami_reader=_whoami_reader` into `create_planner_app`, and change `healthz`'s tenant list to `registry.known_slugs()` when a registry is configured (documenting that it is cache warmth, not the servable set).

Check `tenant_conn`'s exact signature in `pg/db.py` before use and match it (it accepts the tenant uuid and an optional `sub`/role via `tenant_claims`).

- [ ] **Step 4: Run test to verify it passes**

Run the same command. Expected: PASS.
Then regression: `pytest tests/bff tests/pg -q` — everything green.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/bff/ services/agent-spine/tests/pg/test_c5_multi_tenant_serving.py
git commit -m "feat(c5): serve any tenant via the registry; PLANNER_TENANT now optional"
```

---

### Task 7: Empty-tenant hardening

**Files:**
- Modify: whichever read paths crash on zero rows (likely `pg/store.py` aggregates and `bvr/` inputs — determined by the test)
- Test: `services/agent-spine/tests/pg/test_c5_empty_tenant.py`

**Interfaces:**
- Produces: every tenant-scoped read returns a valid empty response for a tenant with zero `part_keys`/`recommendations`. No signature changes.

> This is the first thing a paying customer sees after signup and before their first upload — a crash here is a churn event.

- [ ] **Step 1: Write the failing test**

```python
# services/agent-spine/tests/pg/test_c5_empty_tenant.py
"""A brand-new tenant (no upload yet) must serve clean empty states."""
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.bff.tenant_registry import TenantRegistry

SECRET = "unit-test-secret-0123456789abcdef"


class _V:
    def __init__(self):
        self._v = HsVerifier(SECRET)

    def verify(self, t):
        return self._v.verify(t)


@pytest.fixture()
def empty_client(pg_pool, pg_admin_conn):
    tid = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c5-empty','Empty') returning id").fetchone()[0]
    reg = TenantRegistry(pg_pool)
    app = create_planner_app({}, verifier=_V(), registry=reg,
                             tenant_uuid_for=reg.uuid_for_slug)
    now = datetime.now(UTC)
    tok = jwt.encode(
        {"sub": "u1", "aud": "authenticated", "iat": now, "exp": now + timedelta(minutes=5),
         "tenant_id": str(tid), "tenant_role": "planner"}, SECRET, algorithm="HS256")
    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {tok}"})
    return client


@pytest.mark.parametrize("path", [
    "/recommendations",
    "/dashboard",
    "/forecast",
    "/feeds",
    "/history",
    "/reports/bvr",
])
def test_read_surfaces_serve_empty_state(empty_client, path):
    r = empty_client.get(f"/v1/tenants/c5-empty{path}")
    assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:200]}"


def test_queue_is_an_empty_page(empty_client):
    body = empty_client.get("/v1/tenants/c5-empty/recommendations").json()
    assert body["items"] == [] and body["total"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_c5_empty_tenant.py -v`
Expected: some parametrized cases FAIL (500s) — record which in the commit message.

- [ ] **Step 3: Fix only what the failures show**

For each failing surface, make the zero-row path return the empty/zero value rather than raising — e.g. guard divisions with a zero denominator, and return `0`/`[]`/`None` from aggregates over no rows instead of unpacking a missing row. **Do not restructure** these modules; make the minimal change that yields a valid empty response, matching each endpoint's existing response model.

- [ ] **Step 4: Run test to verify it passes**

Run the same command. Expected: PASS (7 tests).
Then regression: `pytest tests/pg tests/bff -q`.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/
git commit -m "fix(c5): serve clean empty states for a tenant with no data yet"
```

---

## GROUP 4 — Frontend

### Task 8: `apps/web` on `whoami` — delete the build-time tenant map

**Files:**
- Create: `apps/web/src/lib/api/whoami.ts`, `apps/web/src/lib/api/whoami.test.ts`
- Modify: `apps/web/src/lib/auth/useAuth.tsx` (lines ~12, 70, 88 — the `tenantSlugByUuid` reads), `apps/web/src/lib/auth/supabase.ts` (delete `tenantSlugByUuid` + the `VITE_TENANT_SLUGS` parse), `apps/web/src/components/TenantSwitcher.tsx` (lines ~3, 28, 34)
- Test: `apps/web/src/lib/auth/useAuth.test.tsx` (extend), `apps/web/src/components/TenantSwitcher.test.tsx` (extend if present)

**Interfaces:**
- Consumes: `GET /v1/auth/whoami` (Task 5) via the existing `request<T>` helper in `apps/web/src/lib/api/client.ts`.
- Produces:
  ```ts
  export type TenantRef = { tenant_uuid: string; slug: string; name: string; role: string };
  export type Whoami = { user_id: string; active: TenantRef | null; tenants: TenantRef[] };
  export function getWhoami(): Promise<Whoami>;
  ```
  `useAuth()` gains `tenants: TenantRef[]` and keeps `tenantSlug: string | null` (now sourced from `whoami.active.slug`).

- [ ] **Step 1: Write the failing test**

```ts
// apps/web/src/lib/api/whoami.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { getWhoami } from "./whoami";

describe("whoami api", () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it("GETs the whoami route and returns active + tenants", async () => {
    const body = {
      user_id: "u1",
      active: { tenant_uuid: "T1", slug: "acme", name: "Acme", role: "owner" },
      tenants: [{ tenant_uuid: "T1", slug: "acme", name: "Acme", role: "owner" }],
    };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(body), { status: 200 }));
    const out = await getWhoami();
    expect(out.active?.slug).toBe("acme");
    expect(out.tenants).toHaveLength(1);
    const url = (globalThis.fetch as unknown as { mock: { calls: string[][] } }).mock.calls[0][0];
    expect(url).toContain("/v1/auth/whoami");
  });

  it("tolerates the no-membership state", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ user_id: "u1", active: null, tenants: [] }), { status: 200 }));
    const out = await getWhoami();
    expect(out.active).toBeNull();
    expect(out.tenants).toEqual([]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npm test -- whoami.test.ts`
Expected: FAIL — cannot resolve `./whoami`.

- [ ] **Step 3: Implement**

```ts
// apps/web/src/lib/api/whoami.ts
import { request } from "./client";

export type TenantRef = {
  tenant_uuid: string;
  slug: string;
  name: string;
  role: string;
};

export type Whoami = {
  user_id: string;
  active: TenantRef | null;
  tenants: TenantRef[];
};

/** The caller's identity + tenant memberships, straight from the verified
 * token. Replaces C2's build-time VITE_TENANT_SLUGS map, which could not know
 * about tenants created after the last frontend deploy. */
export function getWhoami(): Promise<Whoami> {
  return request<Whoami>("/v1/auth/whoami");
}
```

Then in `useAuth.tsx`: after a session is established, call `getWhoami()`; set `tenantSlug` from `whoami.active?.slug ?? null` and expose `tenants` from `whoami.tenants`; keep calling the existing `setActiveTenant(slug)` so `client.ts` keeps sending the right tenant. On a `whoami` failure, degrade to `tenantSlug: null, tenants: []` (the app already renders the no-tenant-access branch) — do not throw from the provider.

In `supabase.ts`, delete `tenantSlugByUuid`, the `slugMapRaw` parse, and the `VITE_TENANT_SLUGS` reference (including the now-stale comment about C4 replacing it with whoami). In `TenantSwitcher.tsx`, replace `Object.entries(tenantSlugByUuid)` with `useAuth().tenants` (each entry already carries `slug`/`name`/`tenant_uuid`), keeping the existing activate-tenant call and busy/error handling.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npm test -- whoami.test.ts`, then the full suite + build + lint:
`npm test && npm run build && npm run lint`
Expected: all green. Verify the map is gone: `grep -rn "VITE_TENANT_SLUGS\|tenantSlugByUuid" apps/web/src` returns **no matches**.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/api/whoami.ts apps/web/src/lib/api/whoami.test.ts apps/web/src/lib/auth/ apps/web/src/components/TenantSwitcher.tsx
git commit -m "feat(web): resolve the active tenant from /v1/auth/whoami; delete the build-time slug map"
```

---

## GROUP 5 — Recompute

### Task 9: Preserve-mode `seed_store`

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/pg/seed.py` (`_SEEDED_TABLES` loop, ~lines 34-51, and the `seed_store` signature), `services/agent-spine/src/trax_io_spine/pg/ingest.py` (`run_ingest` signature + its `seed_store` call)
- Test: `services/agent-spine/tests/pg/test_c5_preserve_seed.py`

**Interfaces:**
- Produces: `seed_store(pool, *, store, slug, name, preserve: frozenset[str] = frozenset()) -> SeedReport` — tables named in `preserve` are **not** deleted. `run_ingest(conn, pool, payload, *, storage, tenant_name="", preserve: frozenset[str] = frozenset())` threads it through.
- Constraint: the default `frozenset()` keeps upload-ingest full-replace behavior byte-for-byte unchanged.

- [ ] **Step 1: Write the failing test**

```python
# services/agent-spine/tests/pg/test_c5_preserve_seed.py
"""preserve= keeps the audit ledger and the kill switch across a re-seed."""
from datetime import UTC, datetime
from pathlib import Path

from trax_io_spine.bff.store import PlannerStore
from trax_io_spine.pg.seed import seed_store

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)


def _store():
    return PlannerStore.from_extract(
        tenant_id="c5-preserve", extract_dir=str(_SAMPLE),
        now=datetime(2026, 4, 1, tzinfo=UTC))


def _counts(conn, tid):
    return {
        t: conn.execute(f"select count(*) from {t} where tenant_id=%s", (tid,)).fetchone()[0]
        for t in ("writeback_ledger", "kill_switches", "recommendations")
    }


def test_preserve_keeps_ledger_and_killswitch_but_replaces_queue(pg_pool, pg_admin_conn):
    report = seed_store(pg_pool, store=_store(), slug="c5-preserve", name="P")
    tid = report.tenant_uuid
    pg_admin_conn.execute(
        "insert into writeback_ledger (tenant_id,pn,location,version,entry) "
        "values (%s,'P1','JFK',1,'{}'::jsonb)", (tid,))
    pg_admin_conn.execute(
        "insert into kill_switches (tenant_id,engaged) values (%s,true) "
        "on conflict (tenant_id) do update set engaged=true", (tid,))
    before = _counts(pg_admin_conn, tid)
    assert before["writeback_ledger"] == 1 and before["kill_switches"] == 1

    seed_store(pg_pool, store=_store(), slug="c5-preserve", name="P",
               preserve=frozenset({"writeback_ledger", "kill_switches"}))

    after = _counts(pg_admin_conn, tid)
    assert after["writeback_ledger"] == 1, "audit ledger must survive a recompute"
    assert after["kill_switches"] == 1, "kill switch must never be silently reset"
    assert after["recommendations"] == before["recommendations"], "queue is replaced, not doubled"


def test_default_still_full_replaces(pg_pool, pg_admin_conn):
    """Upload-ingest behavior is unchanged: no preserve => everything cleared."""
    report = seed_store(pg_pool, store=_store(), slug="c5-replace", name="R")
    tid = report.tenant_uuid
    pg_admin_conn.execute(
        "insert into writeback_ledger (tenant_id,pn,location,version,entry) "
        "values (%s,'P1','JFK',1,'{}'::jsonb)", (tid,))
    seed_store(pg_pool, store=_store(), slug="c5-replace", name="R")
    assert _counts(pg_admin_conn, tid)["writeback_ledger"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_c5_preserve_seed.py -v`
Expected: FAIL — `seed_store() got an unexpected keyword argument 'preserve'`.

- [ ] **Step 3: Implement**

In `seed.py`:

```python
def seed_store(pool, *, store: PlannerStore, slug: str, name: str,
               preserve: frozenset[str] = frozenset()) -> SeedReport:
    ...
        # C5: a scheduled recompute must never delete the append-only
        # writeback ledger (rollback + SOC 2 audit) or reset the kill switch
        # (a safety control). Upload-ingest passes no `preserve` and keeps
        # full-replace semantics exactly as before.
        for table in _SEEDED_TABLES:
            if table in preserve:
                continue
            conn.execute(  # noqa: S608 — table names from a module constant
                f"delete from {table} where tenant_id = %s::uuid", (tenant_uuid,)
            )
```

In `ingest.py`, add `preserve: frozenset[str] = frozenset()` to `run_ingest` and pass it into the `seed_store(...)` call. Change nothing else in either function.

- [ ] **Step 4: Run test to verify it passes**

Run the same command. Expected: PASS (2 tests).
Then regression (C3 ingest behavior must be untouched): `pytest tests/pg/test_c3_ingest_handler.py -q`.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/pg/seed.py services/agent-spine/src/trax_io_spine/pg/ingest.py services/agent-spine/tests/pg/test_c5_preserve_seed.py
git commit -m "feat(c5): preserve-mode seed — recompute never deletes the ledger or kill switch"
```

---

### Task 10: Worker `recompute` handler

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/pg/worker.py` (`HANDLERS`, beside `_ingest_handler`)
- Test: `services/agent-spine/tests/pg/test_c5_recompute_handler.py`

**Interfaces:**
- Consumes: `run_ingest(..., preserve=…)` (Task 9); the existing `_ingest_handler` plumbing (pool + `HttpxStorageReader` from `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`).
- Produces: `HANDLERS["recompute"]` — same replay as an ingest, with `preserve=frozenset({"writeback_ledger","kill_switches"})`, and the returned `result` tagged `source="recompute"`.

- [ ] **Step 1: Write the failing test**

```python
# services/agent-spine/tests/pg/test_c5_recompute_handler.py
"""The recompute handler replays a payload in preserve-mode and tags its result."""
from trax_io_spine.pg import worker


def test_recompute_handler_is_registered():
    assert "recompute" in worker.HANDLERS


def test_recompute_runs_in_preserve_mode_and_tags_source(monkeypatch):
    seen = {}

    def _fake_run_ingest(conn, pool, payload, *, storage, tenant_name="", preserve=frozenset()):
        seen["preserve"] = preserve
        seen["payload"] = payload
        return {"status": "done", "result": {"keys": 3}}

    monkeypatch.setattr(worker, "run_ingest", _fake_run_ingest)
    monkeypatch.setattr(worker, "_ingest_pool", None, raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")

    class _Conn:
        def execute(self, *a, **k):
            class _R:
                def fetchone(self_inner):
                    return ("Acme",)
            return _R()

    class _Pool:
        def connection(self):
            class _Ctx:
                def __enter__(self_inner):
                    return _Conn()
                def __exit__(self_inner, *a):
                    return False
            return _Ctx()

    monkeypatch.setattr(worker, "make_pool", lambda url: _Pool())

    out = worker.HANDLERS["recompute"]({"tenant_id": "T1", "files": {"parts": "p"}})

    assert seen["preserve"] == frozenset({"writeback_ledger", "kill_switches"})
    assert out["result"]["source"] == "recompute"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_c5_recompute_handler.py -v`
Expected: FAIL — `KeyError: 'recompute'`.

- [ ] **Step 3: Implement**

Refactor `_ingest_handler` so the shared body takes a `preserve` argument, then register both kinds:

```python
_RECOMPUTE_PRESERVE = frozenset({"writeback_ledger", "kill_switches"})


def _run_job(payload: dict, *, preserve: frozenset[str]) -> dict:
    """Shared body for `ingest` and `recompute`: both replay a canonical batch
    from Storage through the engine and re-seed. They differ only in what the
    seed is allowed to delete (see C5 spec §3.1)."""
    global _ingest_pool
    if _ingest_pool is None:
        url = os.environ.get("WORKER_DATABASE_URL") or os.environ["DATABASE_URL"]
        _ingest_pool = make_pool(url)
    storage = HttpxStorageReader(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"]
    )
    with _ingest_pool.connection() as conn:
        row = conn.execute(
            "select name from tenants where id = %s::uuid", (payload["tenant_id"],)
        ).fetchone()
        tenant_name = row[0] if row else ""
        return run_ingest(
            conn, _ingest_pool, payload, storage=storage,
            tenant_name=tenant_name, preserve=preserve,
        )


def _ingest_handler(payload: dict) -> dict:
    return _run_job(payload, preserve=frozenset())


def _recompute_handler(payload: dict) -> dict:
    out = _run_job(payload, preserve=_RECOMPUTE_PRESERVE)
    if isinstance(out, dict) and isinstance(out.get("result"), dict):
        out["result"]["source"] = "recompute"
    return out


HANDLERS["ingest"] = _ingest_handler
HANDLERS["recompute"] = _recompute_handler
```

- [ ] **Step 4: Run test to verify it passes**

Run the same command. Expected: PASS (2 tests).
Then regression: `pytest tests/pg/test_worker.py tests/pg/test_c3_ingest_handler.py -q`.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/pg/worker.py services/agent-spine/tests/pg/test_c5_recompute_handler.py
git commit -m "feat(c5): worker recompute handler (preserve-mode replay of the last batch)"
```

---

### Task 11: Label scheduled recomputes in the ingest history

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/pg/uploads.py` (`IngestJobStore`'s history query — include `kind`), `apps/web/src/lib/api/ingest.ts` (type), `apps/web/src/features/feeds/IngestHistory.tsx`
- Test: `apps/web/src/features/feeds/IngestHistory.test.tsx` (extend)

**Interfaces:**
- Consumes: `jobs.kind ∈ {'ingest','recompute'}`.
- Produces: the ingest-history rows carry `kind`; the UI renders **"Upload"** for `ingest` and **"Scheduled recompute"** for `recompute`.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/features/feeds/IngestHistory.test.tsx  (add)
it("labels scheduled recomputes distinctly from uploads", async () => {
  vi.spyOn(ingestApi, "listIngests").mockResolvedValue([
    { job_id: "1", kind: "ingest", status: "done", created_at: "2026-07-24T00:00:00Z",
      result: { keys: 3 }, errors: null },
    { job_id: "2", kind: "recompute", status: "done", created_at: "2026-07-24T03:00:00Z",
      result: { keys: 3 }, errors: null },
  ] as unknown as ingestApi.IngestJob[]);
  renderHistory();
  expect(await screen.findByText(/scheduled recompute/i)).toBeInTheDocument();
  expect(screen.getByText(/^upload$/i)).toBeInTheDocument();
});
```

Match the file's existing render helper and mock style rather than introducing a new one.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/web && npm test -- IngestHistory.test.tsx`
Expected: FAIL — "Scheduled recompute" is not rendered.

- [ ] **Step 3: Implement**

Add `kind` to the history SQL select and to the row model in `pg/uploads.py`; add `kind: "ingest" | "recompute"` to the `IngestJob` type in `apps/web/src/lib/api/ingest.ts`; in `IngestHistory.tsx` render a label derived from it:

```tsx
const KIND_LABEL: Record<string, string> = {
  ingest: "Upload",
  recompute: "Scheduled recompute",
};
// ...in the row: {KIND_LABEL[job.kind] ?? "Upload"}
```

Defaulting to "Upload" keeps pre-C5 rows (written before `kind` was surfaced) rendering sensibly.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/web && npm test -- IngestHistory.test.tsx`, then `npm test && npm run build && npm run lint`.
Also run the BFF side: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg -q`.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/pg/uploads.py apps/web/src/lib/api/ingest.ts apps/web/src/features/feeds/
git commit -m "feat(c5): distinguish scheduled recomputes from uploads in ingest history"
```

---

## GROUP 6 — Rollout + bookkeeping

### Task 12: C5 rollout runbook (incl. pg_cron) + retire C4's manual-activation limitation

**Files:**
- Create: `deploy/C5_ROLLOUT.md`
- Modify: `deploy/C4_ROLLOUT.md` (delete the "Known limitation — manual tenant activation" section and its cross-references)

**Interfaces:**
- Consumes: migrations 0013–0014 (Tasks 1–2), the whoami/registry deploy (Tasks 3–8), the recompute handler (Task 10).

- [ ] **Step 1: Write the runbook**

`deploy/C5_ROLLOUT.md`, in executable order, each step with the exact command:

1. **Apply migrations** — `supabase db push --db-url "postgresql://postgres.sluoxufnqwusmtckklnv:<DB_PASSWORD>@aws-0-us-east-1.pooler.supabase.com:5432/postgres"` (0013–0014). *Use `aws-0`, verified live — `aws-1` does not resolve.* Verify: `select proname from pg_proc where proname in ('tenants_for_current_user','enqueue_due_recomputes');` returns both.
2. **Redeploy the BFF and worker** — `railway up -s bff` then `railway up -s worker`, run from the checkout holding the C5 code (`railway up` uploads the CWD). The worker must ship before the cron is scheduled, or `recompute` jobs dead-letter as an unknown kind.
3. **Redeploy `apps/web` (prebuilt)** — `cd apps/web && vercel build --prod && vercel deploy --prebuilt --prod --yes` (plain `vercel deploy` fails remotely on the `../../packages/tailwind-preset` import — see `.claude/memory/lessons.md`).
4. **Remove `VITE_TENANT_SLUGS`** from the Vercel project (`vercel env rm VITE_TENANT_SLUGS production`) — the frontend no longer reads it. Redeploy after removal.
5. **Enable + schedule pg_cron** (verified available: 1.6.4, not installed) — as `postgres` over the pooler:
   ```sql
   create extension if not exists pg_cron;
   select cron.schedule('aeronta-nightly-recompute', '0 3 * * *',
                        $$select public.enqueue_due_recomputes()$$);
   ```
   Verify: `select jobid, schedule, command, active from cron.job;`
6. **Dry-run the enqueue once** — `select public.enqueue_due_recomputes();` then `select id, tenant_id, kind, status from jobs where kind='recompute' order by id desc limit 5;` and confirm the worker drains it (`status` → `done`).
7. **Acceptance gate — the point of this whole sub-project:** sign up a brand-new tenant end to end (signup → Stripe checkout → upload → recommendations visible in the app) **with no manual activation step of any kind**. Record the tenant slug used.

Include a short "if pg_cron cannot be installed" fallback note: the SQL function is the seam, so a worker-internal tick calling `enqueue_due_recomputes()` substitutes for the cron without touching anything else.

- [ ] **Step 2: Retire the C4 limitation**

Delete the "Known limitation — manual tenant activation (until C5 multi-tenant serving)" section from `deploy/C4_ROLLOUT.md` and any reference to adding `uuid:slug` to `VITE_TENANT_SLUGS`. Verify: `grep -rn "VITE_TENANT_SLUGS" deploy/` returns only C5's *removal* step.

- [ ] **Step 3: Commit**

```bash
git add deploy/C5_ROLLOUT.md deploy/C4_ROLLOUT.md
git commit -m "ops(c5): rollout runbook (pg_cron + migrations + deploys); retire C4's manual-activation limitation"
```

---

### Task 13: Bookkeeping

**Files:**
- Modify: `ROADMAP.md`, `TASKS.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-07-24-c5-multi-tenant-serving-design.md` (status line), `supabase/README.md` (live-facts: migrations 0001–0014, pg_cron)

- [ ] **Step 1:** `ROADMAP.md` — add a **C5** entry marked code-complete with today's date, summarizing both subsystems, and **remove the C4 carry-forward** that C5 closes (multi-tenant BFF serving + dynamic tenant map). Note the live rollout is pending per `deploy/C5_ROLLOUT.md`.
- [ ] **Step 2:** `TASKS.md` — add a "C5 shipped" section: what was built, the notable review catches, test counts, and the carry-forwards that remain (queue reconciliation §3.6; registry eviction; per-tenant cadence).
- [ ] **Step 3:** `CLAUDE.md` — add a C5 paragraph to Section A: the BFF now serves any tenant via `TenantRegistry` (no `PLANNER_TENANT` requirement), `/v1/auth/whoami` is the tenant-resolution source for `apps/web` (the build-time map is gone), and pg_cron drives nightly `recompute` jobs that replay the last ingest under preserve-mode. Include the **rule**: any new BFF pool reader against a tenant-scoped table must go through `tenant_conn` (the C4 lesson) *and* any new tenant-scoped route must resolve through the registry, never a static dict.
- [ ] **Step 4:** Update the C5 spec status line to `✅ Code complete <date> — live rollout pending`, and `supabase/README.md`'s live-facts row to migrations **0001–0014** plus a pg_cron line.
- [ ] **Step 5: Commit**

```bash
git add ROADMAP.md TASKS.md CLAUDE.md docs/superpowers/specs/2026-07-24-c5-multi-tenant-serving-design.md supabase/README.md
git commit -m "docs: C5 bookkeeping — ROADMAP/TASKS/CLAUDE.md/spec/README"
```

---

## Task dependency & sequencing

- **1, 2** (migrations) are independent of each other; both land first.
- **3** (registry) needs 1's migration only for the DB to exist — it uses `resolve_tenant_slug` (pre-existing).
- **4** (middleware) **must land before 6** — it closes the cross-tenant fallthrough that 6 would otherwise open.
- **5** (whoami) needs 1; **6** needs 3, 4, 5; **7** needs 6.
- **8** (frontend) needs 5 + 6 deployed behavior (tests mock the endpoint, so it can be written once 5's contract exists).
- **9** (preserve seed) needs nothing; **10** needs 9; **11** needs 10's `kind` values.
- **12, 13** last.

Serving spine: **1 → 3 → 4 → 5 → 6 → 7 → 8**. Recompute: **2 → 9 → 10 → 11**. The two tracks are independent after their migrations and could be built in either order.
