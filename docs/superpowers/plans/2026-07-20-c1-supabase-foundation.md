# C1 — Supabase Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Multi-tenant Postgres foundation for the commercial SaaS — tenant/membership schema with RLS, the Supabase claims hook, and a Postgres-backed `PlannerStore` behind the existing interface, so the BFF becomes stateless and tenant-isolated at the data layer.

**Architecture:** Supabase-compatible SQL migrations live in a new repo-root `supabase/migrations/` dir (timestamp-named, appliable by `supabase db push` later; applied in tests by our own runner against a throwaway Postgres container). A new `trax_io_spine.pg` package provides `PgWritebackTarget` (ledger in Postgres) and `PgPlannerStore` (same public interface as `PlannerStore`; queue/decisions live in SQL, static per-key/tenant views are precomputed at seed time by a seeder that reuses the in-memory store). `create_planner_app` is duck-typed (`stores: dict[str, PlannerStore]`) so the Pg store plugs in without app changes.

**Tech Stack:** Python ≥3.12, psycopg 3 (sync, `psycopg[binary,pool]`), testcontainers (Postgres 16 image), pydantic v2 (existing models unchanged), plain SQL migrations (no ORM, no Alembic).

**Spec:** [docs/superpowers/specs/2026-07-20-commercialization-architecture-design.md](../specs/2026-07-20-commercialization-architecture-design.md) — sections 3 (tenancy & auth), 4 (persistence rework), 9 (testing), 10 (C1 row).

## Global Constraints

- Python ≥3.12; `uv` for everything; `ruff check` must stay clean (line-length 100, rules `E,F,I,B,UP,N,SIM`).
- All new code in `services/agent-spine` behind a new `pg` extra: `pg = ["psycopg[binary,pool]>=3.2", "testcontainers[postgres]>=4.7"]`. Test command: `uv run --extra dev --extra bff --extra bvr --extra pg pytest tests/pg -v`. Docker is required for `tests/pg` (testcontainers); tests must `pytest.skip` cleanly when Docker is unavailable, mirroring the repo's Oracle-gated smoke-test pattern.
- **Never touch the `oracle19c` or MySQL containers.** Testcontainers manages its own throwaway Postgres 16 container only.
- Migrations live ONLY in repo-root `supabase/migrations/`, named `2026072000000N_<slug>.sql`. Idempotency is NOT required (each migration runs exactly once, tracked in `public._migrations`).
- **Every tenant-scoped table ships in the same migration as its RLS policy, and every table gets a two-tenant isolation test** (spec §9: the 4-layer convention). A table without RLS is a task failure.
- JWT claims contract (locked here, consumed by C2): custom claims `tenant_id` (uuid as text) and `tenant_role` (`owner|admin|planner|viewer`) stamped by the claims hook. RLS policies read `(select auth.jwt()->>'tenant_id')::uuid` — always wrapped in a scalar subquery (verified RLS performance discipline), and every column referenced by a policy is indexed.
- The BFF database role is `trax_app` (`NOBYPASSRLS`); `service_role` semantics (RLS bypass) are reserved for the seeder/worker path via the `trax_seed` role. No app code path may use a bypassing role.
- Tenant identity: `tenants.id uuid` is the RLS key; `tenants.slug text unique` (e.g. `acme`) is the human/API identity. `PgPlannerStore.tenant_id` remains the slug (interface parity); the uuid is resolved internally once at construction.
- All timestamps `timestamptz`, all JSON `jsonb`. Money stays `numeric` (never float) — existing models carry `Decimal`.
- Commit after every task with the message given in the task's final step; each commit must leave `pytest tests/pg` + the whole existing suite green.
- In-memory `PlannerStore` and its tests are NOT modified except where a task explicitly says so (the seeder imports it; the app tests gain a parametrized fixture).
- Out of scope for C1 (spec §10/§11): JWT verification middleware in the BFF (C2), upload/ingest (C3), Stripe (C4), the `jobs` queue table (C2), SAML per-tenant setup (post-C2).

---

### Task 1: `pg` extra + Postgres test harness + Supabase scaffold

**Files:**
- Modify: `services/agent-spine/pyproject.toml` (add `pg` extra)
- Create: `supabase/README.md`
- Create: `services/agent-spine/tests/pg/__init__.py` (empty)
- Create: `services/agent-spine/tests/pg/auth_shim.sql`
- Create: `services/agent-spine/tests/pg/conftest.py`
- Test: `services/agent-spine/tests/pg/test_harness.py`

**Interfaces:**
- Produces: pytest fixtures `pg_pool` (session-scoped `psycopg_pool.ConnectionPool`, migrations applied, connected as `trax_app`), `admin_pool` (superuser, for seeding/asserting across tenants), and helper `as_tenant(conn, tenant_id: str, role: str = "planner") -> None` (sets `request.jwt.claims` for the current transaction). Every later pg test consumes these.
- Produces: `apply_migrations(conn) -> list[str]` in `conftest.py` (also reused by Task 6's runner test as the reference behavior).

- [ ] **Step 1: Add the `pg` extra**

In `services/agent-spine/pyproject.toml`, extend `[project.optional-dependencies]`:

```toml
pg = ["psycopg[binary,pool]>=3.2", "testcontainers[postgres]>=4.7"]
```

Run: `cd services/agent-spine && uv sync --extra dev --extra bff --extra bvr --extra pg`
Expected: resolves and installs psycopg + testcontainers.

- [ ] **Step 2: Create the Supabase scaffold**

`supabase/README.md`:

```markdown
# Supabase — commercial SaaS data layer (C1+)

`migrations/` holds plain-SQL migrations, timestamp-named, in Supabase CLI layout
(`supabase db push` compatible). They are ALSO applied by the Python test harness
(`services/agent-spine/tests/pg/conftest.py`) against a throwaway Postgres 16
container — no Supabase CLI needed to run tests.

Conventions (see the C1 plan's Global Constraints):
- every tenant-scoped table ships with RLS in the same migration
- RLS reads `(select auth.jwt()->>'tenant_id')::uuid`; policy columns are indexed
- roles: `trax_app` (BFF, NOBYPASSRLS) / `trax_seed` (seeder-worker, BYPASSRLS)

`tests/pg/auth_shim.sql` recreates the minimal `auth` schema (`auth.uid()`,
`auth.jwt()`) and the two roles on plain Postgres. It is test-harness-only and
must NEVER be added to `migrations/` — real Supabase provides `auth.*`.
```

Create the (empty for now) dir `supabase/migrations/` with a `.gitkeep`.

- [ ] **Step 3: Write the auth shim**

`services/agent-spine/tests/pg/auth_shim.sql`:

```sql
-- Test-harness-only Supabase compatibility shim for plain Postgres.
-- Real Supabase provides schema auth + roles; never ship this as a migration.
create schema if not exists auth;

create or replace function auth.jwt() returns jsonb
language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
$$;

create or replace function auth.uid() returns uuid
language sql stable as $$
  select nullif(auth.jwt()->>'sub', '')::uuid
$$;

do $$ begin
  if not exists (select from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
  if not exists (select from pg_roles where rolname = 'trax_app') then
    create role trax_app login password 'trax_app' nobypassrls;
  end if;
  if not exists (select from pg_roles where rolname = 'trax_seed') then
    create role trax_seed login password 'trax_seed' bypassrls;
  end if;
end $$;

grant usage on schema auth to trax_app, trax_seed;
grant execute on all functions in schema auth to trax_app, trax_seed;
```

- [ ] **Step 4: Write the conftest**

`services/agent-spine/tests/pg/conftest.py`:

```python
"""Postgres test harness for the C1 pg layer.

Boots ONE throwaway Postgres 16 container per session (testcontainers), applies
the auth shim + every migration in supabase/migrations/ in name order, then hands
out two pools: `admin_pool` (superuser — seeding + cross-tenant assertions) and
`pg_pool` (role trax_app, NOBYPASSRLS — what the BFF uses; RLS is real here).

Docker-unavailable => whole directory skips (repo convention: env-gated infra
tests skip clean, they never fail the suite).
"""
from __future__ import annotations

from pathlib import Path

import pytest

try:  # docker may be absent (CI matrix, bare laptops)
    from testcontainers.postgres import PostgresContainer

    _DOCKER = True
except Exception:  # pragma: no cover
    _DOCKER = False

import json

MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "supabase" / "migrations"
AUTH_SHIM = Path(__file__).parent / "auth_shim.sql"


def apply_migrations(conn) -> list[str]:
    """Apply every not-yet-applied supabase/migrations/*.sql in name order."""
    conn.execute(
        "create table if not exists public._migrations ("
        "name text primary key, applied_at timestamptz not null default now())"
    )
    applied = {r[0] for r in conn.execute("select name from public._migrations").fetchall()}
    ran: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        conn.execute(path.read_text())
        conn.execute("insert into public._migrations (name) values (%s)", (path.name,))
        ran.append(path.name)
    conn.commit()
    return ran


@pytest.fixture(scope="session")
def _container():
    if not _DOCKER:
        pytest.skip("docker/testcontainers unavailable")
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def admin_pool(_container):
    from psycopg_pool import ConnectionPool

    url = _container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    with ConnectionPool(url, min_size=1, max_size=4) as pool:
        with pool.connection() as conn:
            conn.execute(AUTH_SHIM.read_text())
            conn.commit()
            apply_migrations(conn)
        yield pool


@pytest.fixture(scope="session")
def pg_pool(_container, admin_pool):
    from psycopg_pool import ConnectionPool

    url = _container.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    # swap credentials in the URL for the RLS-enforced app role
    app_url = url.replace(_container.username, "trax_app", 1).replace(
        _container.password, "trax_app", 1
    )
    with ConnectionPool(app_url, min_size=1, max_size=4) as pool:
        yield pool


def as_tenant(conn, tenant_id: str, role: str = "planner", sub: str | None = None) -> None:
    """Impersonate a tenant member for the CURRENT transaction (SET LOCAL)."""
    claims = json.dumps(
        {"sub": sub or "00000000-0000-0000-0000-0000000000aa",
         "tenant_id": tenant_id, "tenant_role": role}
    )
    conn.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
```

- [ ] **Step 5: Write the failing harness test**

`services/agent-spine/tests/pg/test_harness.py`:

```python
"""The harness itself is load-bearing: auth shim functions + roles must exist."""
from tests.pg.conftest import as_tenant


def test_auth_shim_jwt_roundtrip(admin_pool):
    with admin_pool.connection() as conn:
        as_tenant(conn, "11111111-1111-1111-1111-111111111111", role="admin")
        row = conn.execute(
            "select auth.jwt()->>'tenant_id', auth.jwt()->>'tenant_role'"
        ).fetchone()
        assert row == ("11111111-1111-1111-1111-111111111111", "admin")


def test_jwt_empty_outside_transaction_claims(admin_pool):
    with admin_pool.connection() as conn:
        assert conn.execute("select auth.jwt()").fetchone()[0] == {}


def test_app_role_cannot_bypass_rls(pg_pool):
    with pg_pool.connection() as conn:
        row = conn.execute(
            "select rolbypassrls from pg_roles where rolname = current_user"
        ).fetchone()
        assert row[0] is False
```

Note on imports: `tests/pg/__init__.py` exists (repo convention — `tests/bff` has one) so `from tests.pg.conftest import as_tenant` works under the `pythonpath = ["src"]` + rootdir layout. If collection can't resolve it, add `rootdir` conftest re-export instead of changing pythonpath.

- [ ] **Step 6: Run — expect fail/skip-clean, then pass**

Run: `cd services/agent-spine && uv run --extra dev --extra pg pytest tests/pg -v`
Expected first run: PASS with Docker present (3 tests), or `SKIPPED (docker/testcontainers unavailable)` without. If PASS on first run, verify failure honestly by temporarily renaming `auth_shim.sql` → confirm `test_auth_shim_jwt_roundtrip` errors → restore.

- [ ] **Step 7: Ruff + full-suite sanity**

Run: `uv run --extra dev ruff check . && uv run --extra dev --extra bff --extra bvr pytest -q`
Expected: clean; existing 266 passed / 4 skipped unchanged.

- [ ] **Step 8: Commit**

```bash
git add services/agent-spine/pyproject.toml services/agent-spine/tests/pg supabase
git commit -m "feat(pg): C1 Task 1 — pg extra, Postgres test harness, supabase scaffold"
```

---

### Task 2: Migration 0001 — `tenants` + `memberships` + RLS

**Files:**
- Create: `supabase/migrations/20260720000001_tenants_memberships.sql`
- Test: `services/agent-spine/tests/pg/test_tenancy_schema.py`

**Interfaces:**
- Produces: tables `public.tenants(id uuid pk, slug text unique, name text, plan_tier text, key_quota int, created_at)` and `public.memberships(user_id uuid, tenant_id uuid fk, role text, pk(user_id, tenant_id))`; enum-like `role` check constraint `('owner','admin','planner','viewer')`. Later tasks FK `tenant_id` to `tenants(id)`.
- Produces: helper SQL function `public.current_tenant_id() returns uuid` — the single place RLS policies read the claim from (scalar-subquery-friendly, `stable`).

- [ ] **Step 1: Write the migration**

`supabase/migrations/20260720000001_tenants_memberships.sql`:

```sql
-- C1: tenancy core. Spec §3.
create extension if not exists pgcrypto;

create function public.current_tenant_id() returns uuid
language sql stable as $$
  select nullif(auth.jwt()->>'tenant_id', '')::uuid
$$;

create table public.tenants (
  id         uuid primary key default gen_random_uuid(),
  slug       text not null unique check (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  name       text not null,
  plan_tier  text not null default 'trial'
             check (plan_tier in ('trial', 'starter', 'growth', 'scale', 'enterprise')),
  key_quota  integer not null default 5000 check (key_quota > 0),
  created_at timestamptz not null default now()
);

create table public.memberships (
  user_id    uuid not null,
  tenant_id  uuid not null references public.tenants (id) on delete cascade,
  role       text not null check (role in ('owner', 'admin', 'planner', 'viewer')),
  created_at timestamptz not null default now(),
  primary key (user_id, tenant_id)
);
create index memberships_tenant_id_idx on public.memberships (tenant_id);

alter table public.tenants enable row level security;
alter table public.memberships enable row level security;

-- A member sees their own tenant row; only the seed/admin path creates tenants.
create policy tenants_select on public.tenants for select to trax_app, authenticated
  using (id = (select public.current_tenant_id()));

-- A member sees the member list of their active tenant.
create policy memberships_select on public.memberships for select to trax_app, authenticated
  using (tenant_id = (select public.current_tenant_id()));

grant usage on schema public to trax_app, trax_seed;
grant select on public.tenants, public.memberships to trax_app;
grant all on public.tenants, public.memberships to trax_seed;
```

(No insert/update policies for `trax_app` yet — C1's BFF only reads tenancy; tenant provisioning is the C4 signup flow, which runs as `trax_seed`/service.)

- [ ] **Step 2: Write the failing isolation tests**

`services/agent-spine/tests/pg/test_tenancy_schema.py`:

```python
"""Two-tenant isolation for the tenancy core (the 4-layer convention, data layer)."""
import pytest
from tests.pg.conftest import as_tenant

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture(scope="module", autouse=True)
def two_tenants(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values "
            "(%s, 'acme', 'Acme Air'), (%s, 'globex', 'Globex Airways') "
            "on conflict (id) do nothing",
            (A, B),
        )
        conn.execute(
            "insert into memberships (user_id, tenant_id, role) values "
            "('00000000-0000-0000-0000-0000000000aa', %s, 'owner'), "
            "('00000000-0000-0000-0000-0000000000bb', %s, 'owner') "
            "on conflict do nothing",
            (A, B),
        )
        conn.commit()


def test_member_sees_only_own_tenant(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        slugs = [r[0] for r in conn.execute("select slug from tenants").fetchall()]
        assert slugs == ["acme"]


def test_no_claims_sees_nothing(pg_pool):
    with pg_pool.connection() as conn:
        assert conn.execute("select count(*) from tenants").fetchone()[0] == 0


def test_memberships_scoped(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, B)
        rows = conn.execute("select tenant_id::text from memberships").fetchall()
        assert {r[0] for r in rows} == {B}


def test_app_role_cannot_insert_tenants(pg_pool):
    import psycopg

    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("insert into tenants (slug, name) values ('evil', 'Evil')")
```

- [ ] **Step 3: Run to verify fail → apply → pass**

Run: `uv run --extra dev --extra pg pytest tests/pg/test_tenancy_schema.py -v`
Expected: first collection fails on missing tables only if the migration file has an error — the session fixture applies new migrations automatically. Verify the tests pass; then verify honesty: comment out `enable row level security` lines locally → `test_member_sees_only_own_tenant` must FAIL → restore.

- [ ] **Step 4: Ruff + commit**

```bash
uv run --extra dev ruff check .
git add supabase/migrations/20260720000001_tenants_memberships.sql services/agent-spine/tests/pg/test_tenancy_schema.py
git commit -m "feat(pg): C1 Task 2 — tenants/memberships schema with RLS + isolation tests"
```

---

### Task 3: Migration 0002 — custom access token claims hook

**Files:**
- Create: `supabase/migrations/20260720000002_claims_hook.sql`
- Test: `services/agent-spine/tests/pg/test_claims_hook.py`

**Interfaces:**
- Produces: `public.custom_access_token_hook(event jsonb) returns jsonb` — Supabase Auth hook contract: receives `{"user_id": ..., "claims": {...}}`, returns the event with `claims.tenant_id` + `claims.tenant_role` stamped from the user's membership. Single-membership users get theirs; multi-membership users get the most-recently-created membership unless `claims.tenant_id` already names one they belong to (tenant switching, C2's auth shell, works by re-minting with a requested tenant claim).

- [ ] **Step 1: Write the migration**

`supabase/migrations/20260720000002_claims_hook.sql`:

```sql
-- C1: Supabase custom access token hook (spec §3 — claims in token, app_metadata-grade).
-- Registered in the Supabase dashboard/config as the access-token hook in C2 deploy;
-- pure SQL so it is testable on plain Postgres today.
create function public.custom_access_token_hook(event jsonb) returns jsonb
language plpgsql stable as $$
declare
  uid uuid := (event->>'user_id')::uuid;
  requested uuid := nullif(event->'claims'->>'tenant_id', '')::uuid;
  m record;
begin
  select tenant_id, role into m
  from public.memberships
  where user_id = uid
    and (requested is null or tenant_id = requested)
  order by (tenant_id = requested) desc nulls last, created_at desc
  limit 1;

  if m is null then
    -- no membership: strip any tenant claims rather than passing through junk
    return jsonb_set(
      event, '{claims}',
      (event->'claims') - 'tenant_id' - 'tenant_role'
    );
  end if;

  return jsonb_set(
    event, '{claims}',
    (event->'claims')
      || jsonb_build_object('tenant_id', m.tenant_id::text, 'tenant_role', m.role)
  );
end;
$$;

-- Supabase runs hooks as supabase_auth_admin; on the shim, trax_seed suffices.
grant execute on function public.custom_access_token_hook(jsonb) to trax_seed;
```

- [ ] **Step 2: Write the failing tests**

`services/agent-spine/tests/pg/test_claims_hook.py`:

```python
"""The hook is the ONLY writer of tenant claims — pin its selection semantics."""
import json

import pytest

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
U_MULTI = "00000000-0000-0000-0000-0000000000cc"
U_NONE = "00000000-0000-0000-0000-0000000000dd"


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values "
            "(%s, 'acme', 'Acme Air'), (%s, 'globex', 'Globex Airways') "
            "on conflict (id) do nothing",
            (A, B),
        )
        # U_MULTI: member of both; globex membership is newer
        conn.execute(
            "insert into memberships (user_id, tenant_id, role, created_at) values "
            "(%s, %s, 'planner', now() - interval '2 days'), "
            "(%s, %s, 'admin',   now() - interval '1 day') on conflict do nothing",
            (U_MULTI, A, U_MULTI, B),
        )
        conn.commit()


def _hook(conn, user_id: str, claims: dict) -> dict:
    row = conn.execute(
        "select public.custom_access_token_hook(%s::jsonb)",
        (json.dumps({"user_id": user_id, "claims": claims}),),
    ).fetchone()
    return row[0]["claims"]


def test_default_is_most_recent_membership(admin_pool):
    with admin_pool.connection() as conn:
        claims = _hook(conn, U_MULTI, {"sub": U_MULTI})
        assert claims["tenant_id"] == B
        assert claims["tenant_role"] == "admin"


def test_requested_tenant_honored_when_member(admin_pool):
    with admin_pool.connection() as conn:
        claims = _hook(conn, U_MULTI, {"sub": U_MULTI, "tenant_id": A})
        assert claims["tenant_id"] == A
        assert claims["tenant_role"] == "planner"


def test_requested_tenant_ignored_when_not_member(admin_pool):
    with admin_pool.connection() as conn:
        evil = "99999999-9999-9999-9999-999999999999"
        claims = _hook(conn, U_MULTI, {"sub": U_MULTI, "tenant_id": evil})
        # falls back to a REAL membership, never passes the foreign claim through
        assert claims["tenant_id"] in (A, B)


def test_no_membership_strips_claims(admin_pool):
    with admin_pool.connection() as conn:
        claims = _hook(conn, U_NONE, {"sub": U_NONE, "tenant_id": A})
        assert "tenant_id" not in claims and "tenant_role" not in claims
```

- [ ] **Step 3: Run → pass; honesty check**

Run: `uv run --extra dev --extra pg pytest tests/pg/test_claims_hook.py -v`
Expected: 4 PASS. Honesty check: `test_requested_tenant_ignored_when_not_member` is the security-critical one — temporarily drop the `and (requested is null or tenant_id = requested)` guard's fallback ordering and confirm it fails, then restore.

- [ ] **Step 4: Ruff + commit**

```bash
uv run --extra dev ruff check .
git add supabase/migrations/20260720000002_claims_hook.sql services/agent-spine/tests/pg/test_claims_hook.py
git commit -m "feat(pg): C1 Task 3 — custom access token claims hook + selection tests"
```

---

### Task 4: Migration 0003 — planner lifecycle tables (recommendations, decisions, ledger, kill switch)

**Files:**
- Create: `supabase/migrations/20260720000003_planner_lifecycle.sql`
- Test: `services/agent-spine/tests/pg/test_lifecycle_schema.py`

**Interfaces:**
- Produces (all `tenant_id uuid not null references tenants(id)`, all RLS'd):
  - `recommendations(tenant_id, rec_id text, status text, pn text, location text, tier smallint, rec_type text, criticality_tier smallint, confidence numeric, cost_impact numeric, priority numeric, approvable boolean, rec jsonb, outcome jsonb, reject_reason text, reject_detail text, deferred_until timestamptz, decided_at timestamptz, primary key (tenant_id, rec_id))` — `rec` is `Recommendation.model_dump(mode="json")`, `outcome` is `GuardrailOutcome.model_dump(mode="json")`; the scalar columns exist solely for SQL sort/filter/aggregate and are derived from the payloads at insert.
  - `decisions(id bigint generated always as identity, tenant_id, rec_id text, action text check in ('approve','reject','defer','bulk_approve','rollback','kill_switch'), payload jsonb, principal text, at timestamptz default now())` — append-only.
  - `writeback_ledger(tenant_id, pn, location, version int, entry jsonb, changed_at timestamptz, primary key (tenant_id, pn, location, version))` — `entry` is `HistoryEntry.model_dump(mode="json")`; append-only.
  - `kill_switches(tenant_id uuid primary key, engaged boolean not null default false)`.
- Statuses match `TaskStatus` values exactly: `pending|approved|rejected|deferred` (check constraint).

- [ ] **Step 1: Write the migration**

`supabase/migrations/20260720000003_planner_lifecycle.sql`:

```sql
-- C1: durable decision lifecycle (spec §4). Payload JSONB is source of truth;
-- scalar columns are derived query accelerators for the queue's sort/filter.
create table public.recommendations (
  tenant_id        uuid not null references public.tenants (id) on delete cascade,
  rec_id           text not null,
  status           text not null default 'pending'
                   check (status in ('pending', 'approved', 'rejected', 'deferred')),
  pn               text not null,
  location         text not null,
  tier             smallint not null,
  rec_type         text not null,
  criticality_tier smallint not null,
  aog_level        smallint not null default 0,
  confidence       numeric not null,
  cost_impact      numeric not null,
  priority         numeric not null default 0,
  approvable       boolean not null,
  rec              jsonb not null,
  outcome          jsonb not null,
  reject_reason    text,
  reject_detail    text,
  deferred_until   timestamptz,
  decided_at       timestamptz,
  primary key (tenant_id, rec_id)
);
create index recommendations_queue_idx
  on public.recommendations (tenant_id, status, priority desc);
create index recommendations_key_idx on public.recommendations (tenant_id, pn, location);

create table public.decisions (
  id        bigint generated always as identity primary key,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  rec_id    text,
  action    text not null check (action in
            ('approve', 'reject', 'defer', 'bulk_approve', 'rollback', 'kill_switch')),
  payload   jsonb not null default '{}'::jsonb,
  principal text not null default 'planner',
  at        timestamptz not null default now()
);
create index decisions_tenant_idx on public.decisions (tenant_id, at desc);

create table public.writeback_ledger (
  tenant_id  uuid not null references public.tenants (id) on delete cascade,
  pn         text not null,
  location   text not null,
  version    integer not null check (version > 0),
  entry      jsonb not null,
  changed_at timestamptz not null,
  primary key (tenant_id, pn, location, version)
);
create index writeback_ledger_tenant_idx on public.writeback_ledger (tenant_id, pn, location);

create table public.kill_switches (
  tenant_id uuid primary key references public.tenants (id) on delete cascade,
  engaged   boolean not null default false,
  updated_at timestamptz not null default now()
);

alter table public.recommendations enable row level security;
alter table public.decisions enable row level security;
alter table public.writeback_ledger enable row level security;
alter table public.kill_switches enable row level security;

create policy recommendations_rw on public.recommendations for all to trax_app
  using (tenant_id = (select public.current_tenant_id()))
  with check (tenant_id = (select public.current_tenant_id()));
create policy decisions_insert on public.decisions for insert to trax_app
  with check (tenant_id = (select public.current_tenant_id()));
create policy decisions_select on public.decisions for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy ledger_insert on public.writeback_ledger for insert to trax_app
  with check (tenant_id = (select public.current_tenant_id()));
create policy ledger_select on public.writeback_ledger for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy kill_switches_rw on public.kill_switches for all to trax_app
  using (tenant_id = (select public.current_tenant_id()))
  with check (tenant_id = (select public.current_tenant_id()));

-- append-only enforcement: no UPDATE/DELETE policies exist for decisions/ledger,
-- and the grants below don't include them either (belt and braces).
grant select, insert, update, delete on public.recommendations to trax_app;
grant select, insert on public.decisions to trax_app;
grant select, insert on public.writeback_ledger to trax_app;
grant select, insert, update on public.kill_switches to trax_app;
grant usage, select on all sequences in schema public to trax_app;
grant all on public.recommendations, public.decisions,
  public.writeback_ledger, public.kill_switches to trax_seed;
```

- [ ] **Step 2: Write the failing tests**

`services/agent-spine/tests/pg/test_lifecycle_schema.py`:

```python
"""Isolation + append-only guarantees for the lifecycle tables."""
import json

import psycopg
import pytest
from tests.pg.conftest import as_tenant

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _rec_row(tenant: str, rec_id: str) -> tuple:
    rec = {"recommendation_id": rec_id, "part_number": "PN1", "current_location": "MIA"}
    outcome = {"tier": 2}
    return (tenant, rec_id, "PN1", "MIA", 2, "adjust_min_max", 3, 1, 0.9, 1200.5, 10.0,
            True, json.dumps(rec), json.dumps(outcome))


INSERT = (
    "insert into recommendations (tenant_id, rec_id, pn, location, tier, rec_type, "
    "criticality_tier, aog_level, confidence, cost_impact, priority, approvable, rec, outcome) "
    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values "
            "(%s, 'acme', 'Acme Air'), (%s, 'globex', 'Globex Airways') "
            "on conflict (id) do nothing",
            (A, B),
        )
        conn.execute(INSERT, _rec_row(A, "rec-a1"))
        conn.execute(INSERT, _rec_row(B, "rec-b1"))
        conn.commit()


def test_recommendations_isolated(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        ids = [r[0] for r in conn.execute("select rec_id from recommendations").fetchall()]
        assert ids == ["rec-a1"]


def test_cannot_insert_for_other_tenant(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        with pytest.raises(psycopg.errors.RowSecurityViolationError):
            conn.execute(INSERT, _rec_row(B, "rec-b2"))


def test_decisions_append_only(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        conn.execute(
            "insert into decisions (tenant_id, rec_id, action) values (%s, 'rec-a1', 'approve')",
            (A,),
        )
        conn.commit()
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("delete from decisions")


def test_ledger_append_only_and_isolated(pg_pool):
    entry = json.dumps({"status": "written", "new_values": {"rop": 5}})
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        conn.execute(
            "insert into writeback_ledger (tenant_id, pn, location, version, entry, changed_at)"
            " values (%s, 'PN1', 'MIA', 1, %s, now())",
            (A, entry),
        )
        conn.commit()
    with pg_pool.connection() as conn:
        as_tenant(conn, B)
        assert conn.execute("select count(*) from writeback_ledger").fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("update writeback_ledger set changed_at = now()")


def test_kill_switch_scoped(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        conn.execute(
            "insert into kill_switches (tenant_id, engaged) values (%s, true) "
            "on conflict (tenant_id) do update set engaged = true",
            (A,),
        )
        conn.commit()
    with pg_pool.connection() as conn:
        as_tenant(conn, B)
        assert conn.execute("select count(*) from kill_switches").fetchone()[0] == 0
```

- [ ] **Step 3: Run → pass; ruff**

Run: `uv run --extra dev --extra pg pytest tests/pg/test_lifecycle_schema.py -v && uv run --extra dev ruff check .`
Expected: 5 PASS, ruff clean.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/20260720000003_planner_lifecycle.sql services/agent-spine/tests/pg/test_lifecycle_schema.py
git commit -m "feat(pg): C1 Task 4 — lifecycle tables (recommendations/decisions/ledger/kill switch) with RLS"
```

---

### Task 5: Migration 0004 — seeded-view + scenario tables

**Files:**
- Create: `supabase/migrations/20260720000004_views_scenarios.sql`
- Test: `services/agent-spine/tests/pg/test_views_schema.py`

**Interfaces:**
- Produces (all tenant-scoped, RLS'd, same policy shape as Task 4):
  - `part_keys(tenant_id, pn, location, key_stats jsonb, primary key (tenant_id, pn, location))` — `key_stats` is `KeyStats.model_dump(mode="json")` (scenario solver + BVR input).
  - `part_contexts(tenant_id, pn, location, context jsonb, primary key (tenant_id, pn, location))` — `context` is `PartContext.model_dump(mode="json")` (drill-down read).
  - `tenant_snapshots(tenant_id, kind text check in ('dashboard_static','forecast_summary','feeds_summary','current_policies'), payload jsonb, seeded_at timestamptz, primary key (tenant_id, kind))`.
  - `scenarios(tenant_id, scenario_id text, payload jsonb, created_at, primary key (tenant_id, scenario_id))` + `scenario_audit(id identity, tenant_id, event jsonb, at timestamptz)` (append-only).
  - `bvr_cache(tenant_id uuid primary key, report jsonb, computed_at timestamptz)`.

- [ ] **Step 1: Write the migration**

`supabase/migrations/20260720000004_views_scenarios.sql`:

```sql
-- C1: seed-time view payloads + scenarios + BVR cache (spec §4 — heavy compute
-- stays out of the request path; static views are precomputed by pg/seed.py).
create table public.part_keys (
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  pn        text not null,
  location  text not null,
  key_stats jsonb not null,
  primary key (tenant_id, pn, location)
);

create table public.part_contexts (
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  pn        text not null,
  location  text not null,
  context   jsonb not null,
  primary key (tenant_id, pn, location)
);

create table public.tenant_snapshots (
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  kind      text not null check (kind in
            ('dashboard_static', 'forecast_summary', 'feeds_summary', 'current_policies')),
  payload   jsonb not null,
  seeded_at timestamptz not null default now(),
  primary key (tenant_id, kind)
);

create table public.scenarios (
  tenant_id   uuid not null references public.tenants (id) on delete cascade,
  scenario_id text not null,
  payload     jsonb not null,
  created_at  timestamptz not null default now(),
  primary key (tenant_id, scenario_id)
);

create table public.scenario_audit (
  id        bigint generated always as identity primary key,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  event     jsonb not null,
  at        timestamptz not null default now()
);
create index scenario_audit_tenant_idx on public.scenario_audit (tenant_id, at);

create table public.bvr_cache (
  tenant_id   uuid primary key references public.tenants (id) on delete cascade,
  report      jsonb not null,
  computed_at timestamptz not null default now()
);

alter table public.part_keys enable row level security;
alter table public.part_contexts enable row level security;
alter table public.tenant_snapshots enable row level security;
alter table public.scenarios enable row level security;
alter table public.scenario_audit enable row level security;
alter table public.bvr_cache enable row level security;

create policy part_keys_select on public.part_keys for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy part_contexts_select on public.part_contexts for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy tenant_snapshots_select on public.tenant_snapshots for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy scenarios_rw on public.scenarios for all to trax_app
  using (tenant_id = (select public.current_tenant_id()))
  with check (tenant_id = (select public.current_tenant_id()));
create policy scenario_audit_insert on public.scenario_audit for insert to trax_app
  with check (tenant_id = (select public.current_tenant_id()));
create policy scenario_audit_select on public.scenario_audit for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy bvr_cache_rw on public.bvr_cache for all to trax_app
  using (tenant_id = (select public.current_tenant_id()))
  with check (tenant_id = (select public.current_tenant_id()));

grant select on public.part_keys, public.part_contexts, public.tenant_snapshots to trax_app;
grant select, insert, update, delete on public.scenarios to trax_app;
grant select, insert on public.scenario_audit to trax_app;
grant select, insert, update, delete on public.bvr_cache to trax_app;
grant usage, select on all sequences in schema public to trax_app;
grant all on public.part_keys, public.part_contexts, public.tenant_snapshots,
  public.scenarios, public.scenario_audit, public.bvr_cache to trax_seed;
```

- [ ] **Step 2: Write the failing isolation tests**

`services/agent-spine/tests/pg/test_views_schema.py`:

```python
"""Isolation for view/scenario tables; app role is read-only on seeded views."""
import json

import psycopg
import pytest
from tests.pg.conftest import as_tenant

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values "
            "(%s, 'acme', 'Acme Air'), (%s, 'globex', 'Globex Airways') "
            "on conflict (id) do nothing",
            (A, B),
        )
        conn.execute(
            "insert into part_keys (tenant_id, pn, location, key_stats) values "
            "(%s, 'PN1', 'MIA', %s), (%s, 'PN9', 'FRA', %s) on conflict do nothing",
            (A, json.dumps({"unit_cost": 10}), B, json.dumps({"unit_cost": 99})),
        )
        conn.execute(
            "insert into tenant_snapshots (tenant_id, kind, payload) values "
            "(%s, 'forecast_summary', %s) on conflict do nothing",
            (A, json.dumps({"keys": 1})),
        )
        conn.commit()


def test_part_keys_isolated(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        assert [r[0] for r in conn.execute("select pn from part_keys").fetchall()] == ["PN1"]


def test_seeded_views_read_only_for_app(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "insert into part_keys (tenant_id, pn, location, key_stats) "
                "values (%s, 'PN2', 'MIA', '{}')",
                (A,),
            )


def test_snapshots_isolated(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, B)
        assert conn.execute("select count(*) from tenant_snapshots").fetchone()[0] == 0


def test_scenarios_rw_isolated(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, A)
        conn.execute(
            "insert into scenarios (tenant_id, scenario_id, payload) values (%s, 's1', '{}')",
            (A,),
        )
        conn.commit()
    with pg_pool.connection() as conn:
        as_tenant(conn, B)
        assert conn.execute("select count(*) from scenarios").fetchone()[0] == 0
```

- [ ] **Step 3: Run → pass; ruff; commit**

Run: `uv run --extra dev --extra pg pytest tests/pg/test_views_schema.py -v && uv run --extra dev ruff check .`
Expected: 4 PASS, ruff clean.

```bash
git add supabase/migrations/20260720000004_views_scenarios.sql services/agent-spine/tests/pg/test_views_schema.py
git commit -m "feat(pg): C1 Task 5 — seeded-view/scenario/bvr tables with RLS"
```

---

### Task 6: `pg/db.py` — pool, tenant sessions, migration runner

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/pg/__init__.py`
- Create: `services/agent-spine/src/trax_io_spine/pg/db.py`
- Test: `services/agent-spine/tests/pg/test_db.py`

**Interfaces:**
- Produces (consumed by every later task):
  - `make_pool(database_url: str, *, min_size: int = 1, max_size: int = 8) -> ConnectionPool`
  - `apply_migrations(conn, migrations_dir: Path | None = None) -> list[str]` — production twin of the conftest helper (conftest switches to importing THIS from Task 6 on; the conftest copy is deleted in this task).
  - `tenant_claims(tenant_id: str, role: str = "planner", sub: str | None = None) -> str` — canonical claims JSON builder.
  - `@contextmanager tenant_conn(pool, *, tenant_uuid: str, role: str = "planner") -> Iterator[Connection]` — checks out a connection, opens a transaction, `SET LOCAL request.jwt.claims`, yields, commits/rolls back. **The only sanctioned way app code touches Postgres.** (C1 note: the BFF resolves slug→uuid itself and impersonates via this helper because JWT middleware is C2; the helper takes claims, it does not verify them.)
  - `resolve_tenant_uuid(admin_or_seed_conn, slug: str) -> str | None`

- [ ] **Step 1: Write the failing tests**

`services/agent-spine/tests/pg/test_db.py`:

```python
from pathlib import Path

from trax_io_spine.pg.db import apply_migrations, tenant_claims, tenant_conn

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def test_apply_migrations_idempotent(admin_pool):
    with admin_pool.connection() as conn:
        assert apply_migrations(conn) == []  # session fixture already applied all


def test_migrations_dir_default_points_at_repo_root():
    from trax_io_spine.pg import db

    assert (Path(db.DEFAULT_MIGRATIONS_DIR) / "..").resolve().name == "supabase"


def test_tenant_claims_shape():
    import json

    claims = json.loads(tenant_claims(A, role="admin"))
    assert claims["tenant_id"] == A and claims["tenant_role"] == "admin" and "sub" in claims


def test_tenant_conn_sets_and_clears_claims(pg_pool, admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, 'acme', 'Acme Air') "
            "on conflict (id) do nothing",
            (A,),
        )
        conn.commit()
    with tenant_conn(pg_pool, tenant_uuid=A) as conn:
        assert conn.execute("select public.current_tenant_id()::text").fetchone()[0] == A
    # a FRESH checkout has no residual claims (SET LOCAL died with the transaction)
    with pg_pool.connection() as conn:
        assert conn.execute("select public.current_tenant_id()").fetchone()[0] is None
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run --extra dev --extra pg pytest tests/pg/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: trax_io_spine.pg`

- [ ] **Step 3: Implement**

`services/agent-spine/src/trax_io_spine/pg/__init__.py`:

```python
"""Postgres (Supabase) persistence layer for the commercial SaaS (C1)."""
```

`services/agent-spine/src/trax_io_spine/pg/db.py`:

```python
"""Connection + migration plumbing. See C1 plan Task 6 for the contract.

`tenant_conn` is the ONLY sanctioned Postgres entry point for app code: it pins
the tenant's JWT claims onto the transaction with SET LOCAL so every RLS policy
sees them, and they die with the transaction (no leakage across pool checkouts).
"""
from __future__ import annotations

import json
import uuid as _uuid
from contextlib import contextmanager
from pathlib import Path

from psycopg import Connection
from psycopg_pool import ConnectionPool

DEFAULT_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[5] / "supabase" / "migrations"
)


def make_pool(database_url: str, *, min_size: int = 1, max_size: int = 8) -> ConnectionPool:
    return ConnectionPool(database_url, min_size=min_size, max_size=max_size, open=True)


def apply_migrations(conn: Connection, migrations_dir: Path | None = None) -> list[str]:
    """Apply every not-yet-applied migration in name order; returns names ran."""
    mdir = migrations_dir or DEFAULT_MIGRATIONS_DIR
    conn.execute(
        "create table if not exists public._migrations ("
        "name text primary key, applied_at timestamptz not null default now())"
    )
    applied = {r[0] for r in conn.execute("select name from public._migrations").fetchall()}
    ran: list[str] = []
    for path in sorted(mdir.glob("*.sql")):
        if path.name in applied:
            continue
        conn.execute(path.read_text())
        conn.execute("insert into public._migrations (name) values (%s)", (path.name,))
        ran.append(path.name)
    conn.commit()
    return ran


def tenant_claims(tenant_id: str, role: str = "planner", sub: str | None = None) -> str:
    return json.dumps(
        {"sub": sub or str(_uuid.uuid4()), "tenant_id": tenant_id, "tenant_role": role}
    )


@contextmanager
def tenant_conn(pool: ConnectionPool, *, tenant_uuid: str, role: str = "planner"):
    with pool.connection() as conn:
        conn.execute(
            "select set_config('request.jwt.claims', %s, true)",
            (tenant_claims(tenant_uuid, role=role),),
        )
        yield conn
        # pool.connection() context commits on clean exit / rolls back on error


def resolve_tenant_uuid(conn: Connection, slug: str) -> str | None:
    row = conn.execute("select id::text from public.tenants where slug = %s", (slug,)).fetchone()
    return row[0] if row else None
```

- [ ] **Step 4: De-duplicate the conftest**

In `tests/pg/conftest.py`: delete the local `apply_migrations` and `MIGRATIONS_DIR`, import `from trax_io_spine.pg.db import apply_migrations`, and re-export `as_tenant` unchanged. Run the WHOLE pg suite to prove nothing broke.

- [ ] **Step 5: Run → pass; ruff; commit**

Run: `uv run --extra dev --extra pg pytest tests/pg -v && uv run --extra dev ruff check .`
Expected: all pg tests PASS, ruff clean.

```bash
git add services/agent-spine/src/trax_io_spine/pg services/agent-spine/tests/pg
git commit -m "feat(pg): C1 Task 6 — db module (pool, tenant_conn, migration runner)"
```

---

### Task 7: `PgWritebackTarget` — the audited ledger in Postgres

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/pg/writeback.py`
- Test: `services/agent-spine/tests/pg/test_pg_writeback.py`

**Interfaces:**
- Consumes: `tenant_conn` (Task 6); tables `writeback_ledger` (Task 4); existing models `WritebackRequest`, `WritebackResult`, `WritebackStatus`, `HistoryEntry`, `RollbackRequest`, `RollbackResult`, `RollbackStatus` from `trax_io_spine.writeback.contracts` (same imports `writeback/target.py` uses — copy its import block).
- Produces: `class PgWritebackTarget` satisfying the `AuditedWritebackTarget` protocol: `write(req) -> WritebackResult`, `get_history(*, tenant_id, pn, location) -> tuple[HistoryEntry, ...]`, `iter_history(tenant_id) -> tuple[HistoryEntry, ...]`, `rollback(req) -> RollbackResult`. Constructor: `PgWritebackTarget(pool, *, tenant_uuid: str, open_orders: set[tuple[str, str, str]] | None = None, rollback_window_days: int = 90)`.
- Semantics contract (mirrors `InMemoryWritebackTarget`, `writeback/target.py:35-180`, byte-for-byte where observable):
  - current levels per key = `new_values` of the latest `WRITTEN` ledger entry (no separate levels table);
  - idempotency: a ledger entry with the same `entry->>'idempotency_key'` replays its stored result; a `DEFERRED_OPEN_ORDER` result is deterministic from `open_orders` and is NOT persisted (matches in-memory: deferred writes never `_record`);
  - `version` = per-key max+1, assigned inside the transaction; `parent_version` = version of the latest prior `WRITTEN` entry;
  - shadow writes record `SHADOWED` entries and do NOT change current levels;
  - rollback: latest `WRITTEN` entry with non-null `old_values`, window-checked, records a new `WRITTEN` entry with `provenance_id='rollback:<prior>'` — copy the in-memory implementation's field-by-field construction.

- [ ] **Step 1: Write the failing tests**

`services/agent-spine/tests/pg/test_pg_writeback.py`:

```python
"""Conformance of PgWritebackTarget with InMemoryWritebackTarget semantics.

Every test here runs the SAME scenario against both targets and asserts the
observable results match — the in-memory target is the executable spec.
"""
from datetime import UTC, datetime, timedelta

import pytest
from tests.pg.conftest import as_tenant  # noqa: F401  (fixtures)
from trax_io_spine.writeback.contracts import (
    RollbackRequest,
    RollbackStatus,
    WritebackRequest,
    WritebackStatus,
)
from trax_io_spine.writeback.target import InMemoryWritebackTarget

from trax_io_spine.pg.writeback import PgWritebackTarget

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
SLUG = "acme"


def _req(pn="PN1", loc="MIA", *, idem="k1", shadow=False, rop=5):
    return WritebackRequest(
        tenant_id=SLUG, pn=pn, location=loc, rop=rop, eoq=10, safety_stock=2,
        max_stock=20, provenance_id="prov-1", idempotency_key=idem, tier=2,
        shadow=shadow,
    )


@pytest.fixture()
def targets(pg_pool, admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, %s, 'Acme Air') "
            "on conflict (id) do nothing",
            (A, SLUG),
        )
        conn.execute("delete from writeback_ledger where tenant_id = %s", (A,))
        conn.commit()
    return (
        InMemoryWritebackTarget(),
        PgWritebackTarget(pg_pool, tenant_uuid=A),
    )


def test_write_then_history_matches(targets):
    mem, pg = targets
    for t in (mem, pg):
        r1 = t.write(_req(idem="k1", rop=5))
        r2 = t.write(_req(idem="k2", rop=7))
        assert (r1.status, r1.old_values, r2.old_values["rop"]) == (
            WritebackStatus.WRITTEN, None, 5,
        )
    mh = mem.get_history(tenant_id=SLUG, pn="PN1", location="MIA")
    ph = pg.get_history(tenant_id=SLUG, pn="PN1", location="MIA")
    assert [(e.version, e.status, e.parent_version) for e in mh] == [
        (e.version, e.status, e.parent_version) for e in ph
    ]


def test_idempotent_replay(targets):
    _, pg = targets
    first = pg.write(_req(idem="same"))
    again = pg.write(_req(idem="same"))
    assert again.status is WritebackStatus.WRITTEN
    assert again.new_values == first.new_values


def test_open_order_defers_without_ledger_entry(pg_pool, admin_pool, targets):
    _, _ = targets
    pg = PgWritebackTarget(pg_pool, tenant_uuid=A, open_orders={(SLUG, "PN1", "MIA")})
    r = pg.write(_req(idem="k-def"))
    assert r.status is WritebackStatus.DEFERRED_OPEN_ORDER
    assert pg.get_history(tenant_id=SLUG, pn="PN1", location="MIA") == ()


def test_shadow_records_but_does_not_change_levels(targets):
    _, pg = targets
    pg.write(_req(idem="k1", rop=5))
    pg.write(_req(idem="k-sh", rop=9, shadow=True))
    written = pg.write(_req(idem="k3", rop=11))
    assert written.old_values["rop"] == 5  # shadow write did not become current
    statuses = [e.status for e in pg.get_history(tenant_id=SLUG, pn="PN1", location="MIA")]
    assert WritebackStatus.SHADOWED in statuses


def test_rollback_parity(targets):
    mem, pg = targets
    now = datetime.now(UTC)
    for t in (mem, pg):
        t.write(_req(idem="k1", rop=5))
        t.write(_req(idem="k2", rop=7))
        res = t.rollback(RollbackRequest(
            tenant_id=SLUG, pn="PN1", location="MIA",
            principal="planner", reason="test", requested_at=now,
        ))
        assert res.status is RollbackStatus.ROLLED_BACK
        assert res.to_values["rop"] == 5
    assert pg.rollback(RollbackRequest(
        tenant_id=SLUG, pn="PN9", location="ZZZ",
        principal="planner", reason="test", requested_at=now,
    )).status is RollbackStatus.NOTHING_TO_REVERT


def test_rollback_outside_window(pg_pool, targets):
    _, pg = targets
    pg.write(_req(idem="k1", rop=5))
    pg.write(_req(idem="k2", rop=7))
    res = pg.rollback(RollbackRequest(
        tenant_id=SLUG, pn="PN1", location="MIA", principal="planner",
        reason="test", requested_at=datetime.now(UTC) + timedelta(days=91),
    ))
    assert res.status is RollbackStatus.OUTSIDE_WINDOW
```

NOTE for implementer: check `RollbackRequest`'s actual required fields in `trax_io_spine/writeback/contracts.py` before writing — if `reason` is optional or named differently, follow the contract file, and keep the test aligned.

- [ ] **Step 2: Run to verify fail**

Run: `uv run --extra dev --extra pg pytest tests/pg/test_pg_writeback.py -v`
Expected: FAIL — `ModuleNotFoundError: trax_io_spine.pg.writeback`

- [ ] **Step 3: Implement**

`services/agent-spine/src/trax_io_spine/pg/writeback.py`:

```python
"""AuditedWritebackTarget backed by the writeback_ledger table (C1 Task 7).

Executable spec: InMemoryWritebackTarget (writeback/target.py). Observable
behavior must match it — history shape, idempotent replay, shadow semantics,
rollback linking. Current levels are DERIVED (latest WRITTEN entry's new_values)
rather than stored, so the ledger stays the single source of truth.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from trax_io_spine.writeback.contracts import (
    HistoryEntry,
    RollbackRequest,
    RollbackResult,
    RollbackStatus,
    WritebackRequest,
    WritebackResult,
    WritebackStatus,
)
from trax_io_spine.writeback.target import _AGENT_VERSION, _FIELDS

from .db import tenant_conn

_SELECT = (
    "select entry from writeback_ledger "
    "where tenant_id = %s and pn = %s and location = %s order by version"
)


class PgWritebackTarget:
    def __init__(
        self,
        pool,
        *,
        tenant_uuid: str,
        open_orders: set[tuple[str, str, str]] | None = None,
        rollback_window_days: int = 90,
    ) -> None:
        if rollback_window_days <= 0:
            raise ValueError("rollback_window_days must be > 0")
        self._pool = pool
        self._tenant_uuid = tenant_uuid
        self._open_orders = open_orders or set()
        self._window = rollback_window_days

    # -- readers ------------------------------------------------------------
    def _entries(self, conn, pn: str, location: str) -> list[HistoryEntry]:
        rows = conn.execute(_SELECT, (self._tenant_uuid, pn, location)).fetchall()
        return [HistoryEntry.model_validate(r[0]) for r in rows]

    def get_history(self, *, tenant_id: str, pn: str, location: str) -> tuple[HistoryEntry, ...]:
        with tenant_conn(self._pool, tenant_uuid=self._tenant_uuid) as conn:
            return tuple(self._entries(conn, pn, location))

    def iter_history(self, tenant_id: str) -> tuple[HistoryEntry, ...]:
        with tenant_conn(self._pool, tenant_uuid=self._tenant_uuid) as conn:
            rows = conn.execute(
                "select entry from writeback_ledger where tenant_id = %s "
                "order by pn, location, version",
                (self._tenant_uuid,),
            ).fetchall()
            return tuple(HistoryEntry.model_validate(r[0]) for r in rows)

    # -- helpers ------------------------------------------------------------
    def _insert(self, conn, entry: HistoryEntry) -> None:
        conn.execute(
            "insert into writeback_ledger (tenant_id, pn, location, version, entry, changed_at)"
            " values (%s, %s, %s, %s, %s, %s)",
            (self._tenant_uuid, entry.pn, entry.location, entry.version,
             json.dumps(entry.model_dump(mode="json")), entry.changed_at),
        )

    def _record(self, conn, *, req: WritebackRequest, status: WritebackStatus,
                old_values, new_values, principal: str, changed_at: datetime) -> HistoryEntry:
        entries = self._entries(conn, req.pn, req.location)
        version = len(entries) + 1
        parent = next(
            (e.version for e in reversed(entries) if e.status is WritebackStatus.WRITTEN), None
        )
        entry = HistoryEntry(
            tenant_id=req.tenant_id, pn=req.pn, location=req.location, version=version,
            status=status, old_values=old_values, new_values=new_values,
            provenance_id=req.provenance_id, tier=req.tier, agent_version=_AGENT_VERSION,
            changed_by_principal=principal, idempotency_key=req.idempotency_key,
            parent_version=parent, changed_at=changed_at,
        )
        self._insert(conn, entry)
        return entry

    @staticmethod
    def _current_levels(entries: list[HistoryEntry]) -> dict[str, int] | None:
        latest = next(
            (e for e in reversed(entries) if e.status is WritebackStatus.WRITTEN), None
        )
        return dict(latest.new_values) if latest else None

    def _replay(self, conn, idempotency_key: str) -> WritebackResult | None:
        row = conn.execute(
            "select entry from writeback_ledger where tenant_id = %s "
            "and entry->>'idempotency_key' = %s order by version desc limit 1",
            (self._tenant_uuid, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        e = HistoryEntry.model_validate(row[0])
        return WritebackResult(
            tenant_id=e.tenant_id, pn=e.pn, location=e.location, status=e.status,
            old_values=e.old_values, new_values=e.new_values, written_at=e.changed_at,
        )

    # -- protocol -----------------------------------------------------------
    def write(self, req: WritebackRequest) -> WritebackResult:
        with tenant_conn(self._pool, tenant_uuid=self._tenant_uuid) as conn:
            replayed = self._replay(conn, req.idempotency_key)
            if replayed is not None:
                return replayed
            key = (req.tenant_id, req.pn, req.location)
            if not req.shadow and key in self._open_orders:
                return WritebackResult(
                    tenant_id=req.tenant_id, pn=req.pn, location=req.location,
                    status=WritebackStatus.DEFERRED_OPEN_ORDER,
                )
            entries = self._entries(conn, req.pn, req.location)
            old_values = self._current_levels(entries)
            new_values = {f: getattr(req, f) for f in _FIELDS}
            now = datetime.now(UTC)
            status = WritebackStatus.SHADOWED if req.shadow else WritebackStatus.WRITTEN
            self._record(
                conn, req=req, status=status, old_values=old_values,
                new_values=new_values, principal="agent-spine", changed_at=now,
            )
            return WritebackResult(
                tenant_id=req.tenant_id, pn=req.pn, location=req.location, status=status,
                old_values=old_values, new_values=new_values, written_at=now,
            )

    def rollback(self, req: RollbackRequest) -> RollbackResult:
        with tenant_conn(self._pool, tenant_uuid=self._tenant_uuid) as conn:
            entries = self._entries(conn, req.pn, req.location)
            latest = next(
                (e for e in reversed(entries) if e.status is WritebackStatus.WRITTEN), None
            )
            base = dict(tenant_id=req.tenant_id, pn=req.pn, location=req.location)
            if latest is None or latest.old_values is None:
                return RollbackResult(**base, status=RollbackStatus.NOTHING_TO_REVERT)
            if req.requested_at - latest.changed_at > timedelta(days=self._window):
                return RollbackResult(**base, status=RollbackStatus.OUTSIDE_WINDOW)
            current = self._current_levels(entries)
            to_values = dict(latest.old_values)
            entry = self._record(
                conn,
                req=WritebackRequest(
                    tenant_id=req.tenant_id, pn=req.pn, location=req.location,
                    rop=to_values["rop"], eoq=to_values["eoq"],
                    safety_stock=to_values["safety_stock"], max_stock=to_values["max_stock"],
                    provenance_id=f"rollback:{latest.provenance_id}",
                    idempotency_key=f"rollback:{latest.version}:{req.requested_at.isoformat()}",
                    tier=latest.tier,
                ),
                status=WritebackStatus.WRITTEN, old_values=current, new_values=to_values,
                principal=req.principal, changed_at=req.requested_at,
            )
            return RollbackResult(
                **base, status=RollbackStatus.ROLLED_BACK, from_values=current,
                to_values=to_values, reverted_from_version=latest.version,
                new_version=entry.version, rolled_back_at=req.requested_at,
            )
```

If `_AGENT_VERSION`/`_FIELDS` are name-mangled or unimportable, re-export them from `writeback/target.py` (one-line change) rather than duplicating values.

- [ ] **Step 4: Run → pass; ruff; commit**

Run: `uv run --extra dev --extra pg pytest tests/pg/test_pg_writeback.py -v && uv run --extra dev ruff check .`
Expected: 6 PASS, ruff clean.

```bash
git add services/agent-spine/src/trax_io_spine/pg/writeback.py services/agent-spine/tests/pg/test_pg_writeback.py
git commit -m "feat(pg): C1 Task 7 — PgWritebackTarget (ledger in Postgres, in-memory conformance)"
```

---

### Task 8: Seeder — snapshot dir → Postgres

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/pg/seed.py`
- Modify: `services/agent-spine/pyproject.toml` (add script `trax-io-pg-seed = "trax_io_spine.pg.seed:main"`)
- Test: `services/agent-spine/tests/pg/test_seed.py`

**Interfaces:**
- Consumes: `PlannerStore.from_snapshot_dir` (the in-memory store is the computation engine — the seeder never re-implements views), Tasks 4–6 tables/helpers.
- Produces: `seed_tenant(pool, *, slug: str, name: str, snapshot_dir: str) -> SeedReport` where `SeedReport` is a small frozen dataclass `(tenant_uuid: str, recommendations: int, ledger_entries: int, part_keys: int, part_contexts: int)`. Upserts the tenant by slug; replaces (delete+insert) all seeded data for that tenant in ONE transaction. Pool must be a `trax_seed`/admin pool (BYPASSRLS — this is the sanctioned service path, spec §3).
- Produces: CLI `trax-io-pg-seed --database-url URL --tenant SLUG --name NAME --snapshot-dir DIR`.

- [ ] **Step 1: Write the failing test**

`services/agent-spine/tests/pg/test_seed.py`:

```python
"""Seed the committed sample snapshot into Postgres and assert row counts/shape.

Uses the same tiny sample data the BFF tests use (built via from_extract →
precompute pattern is heavyweight here, so we seed FROM a store built off the
sample extract, exercising the same code path from_snapshot_dir feeds into).
"""
from datetime import UTC, datetime
from pathlib import Path

import pytest
from trax_io_spine.bff.store import PlannerStore

from trax_io_spine.pg.seed import seed_store, seed_tenant  # noqa: F401

EXTRACT = (
    Path(__file__).resolve().parents[3]
    / "recommendation-engine" / "examples" / "extract_sample"
)


@pytest.fixture(scope="module")
def sample_store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )


def test_seed_store_writes_everything(admin_pool, sample_store):
    report = seed_store(admin_pool, store=sample_store, slug="acme", name="Acme Air")
    assert report.recommendations == len(sample_store._entries)
    assert report.part_keys == len(sample_store._key_stats())
    assert report.part_contexts == report.part_keys
    with admin_pool.connection() as conn:
        kinds = {
            r[0] for r in conn.execute(
                "select kind from tenant_snapshots where tenant_id = %s::uuid",
                (report.tenant_uuid,),
            ).fetchall()
        }
        assert kinds == {
            "dashboard_static", "forecast_summary", "feeds_summary", "current_policies"
        }


def test_seed_is_replace_idempotent(admin_pool, sample_store):
    r1 = seed_store(admin_pool, store=sample_store, slug="acme", name="Acme Air")
    r2 = seed_store(admin_pool, store=sample_store, slug="acme", name="Acme Air")
    assert (r1.tenant_uuid, r1.recommendations) == (r2.tenant_uuid, r2.recommendations)
    with admin_pool.connection() as conn:
        n = conn.execute(
            "select count(*) from recommendations where tenant_id = %s::uuid",
            (r2.tenant_uuid,),
        ).fetchone()[0]
        assert n == r2.recommendations  # replaced, not doubled
```

- [ ] **Step 2: Run to verify fail**

Run: `uv run --extra dev --extra bff --extra pg pytest tests/pg/test_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: trax_io_spine.pg.seed`

- [ ] **Step 3: Implement**

`services/agent-spine/src/trax_io_spine/pg/seed.py`:

```python
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
            [(tenant_uuid, ks.pn, ks.location, _dump(ks)) for ks in key_stats],
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
```

Private-attr note (`store._entries`, `store._key_stats()`, `store._priority`, `store._manifest`): the seeder is deliberately a friend module of the in-memory store — same package, same maintainers. If ruff flags private access (`SLF001` is NOT in the enabled rule set, so it won't), do not restructure.

If `KeyStats` turns out to be a plain dataclass rather than pydantic (check `bff/scenario.py`), replace `_dump(ks)` for key_stats with `json.dumps(dataclasses.asdict(ks))` and `KeyStats(**row)` on the read side (Task 12).

- [ ] **Step 4: Run → pass; ruff; commit**

Run: `uv run --extra dev --extra bff --extra bvr --extra pg pytest tests/pg/test_seed.py -v && uv run --extra dev ruff check .`
Expected: 2 PASS (sample extract has 6 recommendations), ruff clean.

```bash
git add services/agent-spine/src/trax_io_spine/pg/seed.py services/agent-spine/pyproject.toml services/agent-spine/tests/pg/test_seed.py
git commit -m "feat(pg): C1 Task 8 — snapshot->Postgres seeder + trax-io-pg-seed CLI"
```

---

### Task 9: `PgPlannerStore` scaffold + queue reads

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/store.py` (extract two pure view functions — behavior-preserving refactor)
- Create: `services/agent-spine/src/trax_io_spine/pg/store.py`
- Test: `services/agent-spine/tests/pg/test_pg_store_queue.py`

**Interfaces:**
- Consumes: seeded data (Task 8), `tenant_conn` (Task 6), `PgWritebackTarget` (Task 7).
- Produces (in `bff/store.py`): module-level `row_view(rec, outcome, status, priority) -> QueueRow` and `detail_view(rec, outcome, status) -> RecommendationDetail` — the EXACT bodies of today's `PlannerStore._row` / `detail` with `entry.*` replaced by parameters (`priority` passed in for `row_view`; `detail_view` computes nothing store-bound — `humanize_guardrail_codes(outcome.reasons)` moves with it). `PlannerStore._row`/`detail` become one-line delegations. Existing BFF tests are the refactor's regression gate — zero behavior change.
- Produces (in `pg/store.py`): `class PgPlannerStore` with constructor `PgPlannerStore(pool, *, tenant_slug: str, tenant_uuid: str, open_orders=None)` and — this task — `queue`, `list_queue_page`, `list_queue_all`, `detail`, plus `tenant_id` (the slug, attribute parity with `PlannerStore`) and the same exceptions (`RecommendationNotFound` imported from `bff/store.py`). Signatures are copied EXACTLY from `bff/store.py:442-501` (same keyword names, same defaults, same return types).
- Sort/filter parity contract: SQL `order by <sort_col> {asc|desc}, rec_id asc` reproduces the in-memory two-pass stable sort; sort columns map `priority→priority, cost_impact→cost_impact, confidence→confidence, criticality→criticality_tier`; filters map to `tier = %s`, `rec_type = %s`, `aog_level >= %s`.

- [ ] **Step 1: Extract the pure view functions**

In `bff/store.py`, add module-level functions directly above `class PlannerStore` (bodies lifted verbatim from `_row`/`detail`):

```python
def row_view(rec, outcome, status: TaskStatus, priority: float) -> QueueRow:
    return QueueRow(
        recommendation_id=rec.recommendation_id, pn=rec.part_number,
        location=rec.current_location, type=rec.type, criticality_tier=rec.criticality_tier,
        aog_risk_level=rec.aog_risk_level, confidence_score=rec.confidence_score,
        recommended_quantity=rec.recommended_quantity,
        estimated_cost_impact=rec.estimated_cost_impact, tier=outcome.tier,
        priority_score=priority, status=status,
        reason=rec.reason,
        approvable=rec.policy is not None,
        description=rec.description,
        current_stock=rec.current_stock,
        shortage_quantity=rec.shortage_quantity,
        recommended_location=rec.recommended_location,
        horizon_days=rec.horizon_days,
    )


def detail_view(rec, outcome, status: TaskStatus) -> RecommendationDetail:
    return RecommendationDetail(
        recommendation_id=rec.recommendation_id, pn=rec.part_number,
        location=rec.current_location, type=rec.type, criticality_tier=rec.criticality_tier,
        aog_risk_level=rec.aog_risk_level, confidence_score=rec.confidence_score,
        recommended_quantity=rec.recommended_quantity,
        estimated_cost_impact=rec.estimated_cost_impact, tier=outcome.tier,
        status=status, reason=rec.reason,
        provenance_id=rec.policy.provenance_id if rec.policy else None,
        projected_demand=rec.projected_demand,
        current_policy=_policy_view(rec.current_policy),
        proposed_policy=_policy_view(rec.policy),
        supporting_evidence=tuple(
            _EvidenceView(
                kind=str(e.kind), ref_id=e.ref_id, detail=e.detail,
                as_of=e.as_of.isoformat() if e.as_of else None,
            )
            for e in rec.supporting_evidence
        ),
        guardrail_flags=rec.guardrail_flags,
        guardrail_notes=humanize_guardrail_codes(outcome.reasons),
        description=rec.description,
        current_stock=rec.current_stock,
        shortage_quantity=rec.shortage_quantity,
        recommended_location=rec.recommended_location,
        horizon_days=rec.horizon_days,
    )
```

Replace `PlannerStore._row` body with `return row_view(entry.rec, entry.outcome, entry.status, self._priority(entry))` and `PlannerStore.detail` body with `entry = self._get(rec_id); return detail_view(entry.rec, entry.outcome, entry.status)`.

Run: `uv run --extra dev --extra bff --extra bvr pytest tests/bff -q`
Expected: all pass — refactor is invisible.

- [ ] **Step 2: Write the failing queue tests**

`services/agent-spine/tests/pg/test_pg_store_queue.py`:

```python
"""Queue-read parity: PgPlannerStore vs the in-memory store over the SAME seed."""
from datetime import UTC, datetime
from pathlib import Path

import pytest
from trax_io_spine.bff.models import QueueSortKey, TaskStatus
from trax_io_spine.bff.store import PlannerStore, RecommendationNotFound

from trax_io_spine.pg.seed import seed_store
from trax_io_spine.pg.store import PgPlannerStore

EXTRACT = (
    Path(__file__).resolve().parents[3]
    / "recommendation-engine" / "examples" / "extract_sample"
)


@pytest.fixture(scope="module")
def mem_store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )


@pytest.fixture(scope="module")
def pg_store(admin_pool, pg_pool, mem_store):
    report = seed_store(admin_pool, store=mem_store, slug="acme", name="Acme Air")
    return PgPlannerStore(pg_pool, tenant_slug="acme", tenant_uuid=report.tenant_uuid)


def _ids(rows):
    return [r.recommendation_id for r in rows]


def test_default_queue_parity(mem_store, pg_store):
    assert _ids(pg_store.queue()) == _ids(mem_store.queue())


def test_paging_and_total_parity(mem_store, pg_store):
    m_rows, m_total = mem_store.list_queue_page(limit=2, offset=1)
    p_rows, p_total = pg_store.list_queue_page(limit=2, offset=1)
    assert (_ids(p_rows), p_total) == (_ids(m_rows), m_total)


@pytest.mark.parametrize("sort_by", list(QueueSortKey))
@pytest.mark.parametrize("sort_dir", ["asc", "desc"])
def test_sort_parity_all_keys(mem_store, pg_store, sort_by, sort_dir):
    m = mem_store.list_queue_all(sort_by=sort_by, sort_dir=sort_dir)
    p = pg_store.list_queue_all(sort_by=sort_by, sort_dir=sort_dir)
    assert _ids(p) == _ids(m)


def test_status_filter_parity(mem_store, pg_store):
    for status in (TaskStatus.PENDING, TaskStatus.APPROVED, TaskStatus.REJECTED):
        assert _ids(pg_store.list_queue_all(status=status)) == _ids(
            mem_store.list_queue_all(status=status)
        )


def test_detail_parity(mem_store, pg_store):
    rid = mem_store.queue()[0].recommendation_id
    assert pg_store.detail(rid) == mem_store.detail(rid)


def test_detail_unknown_raises(pg_store):
    with pytest.raises(RecommendationNotFound):
        pg_store.detail("nope")
```

- [ ] **Step 3: Run to verify fail**

Run: `uv run --extra dev --extra bff --extra bvr --extra pg pytest tests/pg/test_pg_store_queue.py -v`
Expected: FAIL — `ModuleNotFoundError: trax_io_spine.pg.store`

- [ ] **Step 4: Implement the scaffold + queue reads**

`services/agent-spine/src/trax_io_spine/pg/store.py`:

```python
"""PgPlannerStore — the PlannerStore interface over Supabase Postgres (C1).

Same public surface as bff/store.PlannerStore (duck-typed into
create_planner_app); queue/decision state lives in SQL, static views are
seeded JSONB (pg/seed.py). This file grows over Tasks 9-12; each section is
labeled with its task.
"""
from __future__ import annotations

import json

from trax_io_spine.bff.models import (
    QueueRow,
    QueueSortKey,
    RecommendationDetail,
    TaskStatus,
)
from trax_io_spine.bff.store import (
    RecommendationNotFound,
    detail_view,
    row_view,
)
from trax_io_reco.contracts import Recommendation

from .db import tenant_conn
from .writeback import PgWritebackTarget

_SORT_COLS = {
    QueueSortKey.PRIORITY: "priority",
    QueueSortKey.COST_IMPACT: "cost_impact",
    QueueSortKey.CONFIDENCE: "confidence",
    QueueSortKey.CRITICALITY: "criticality_tier",
}
_ROW_COLS = "rec, outcome, status, priority"


class PgPlannerStore:
    def __init__(self, pool, *, tenant_slug: str, tenant_uuid: str, open_orders=None):
        self._pool = pool
        self.tenant_id = tenant_slug  # attribute parity with PlannerStore
        self._uuid = tenant_uuid
        self.writeback = PgWritebackTarget(
            pool, tenant_uuid=tenant_uuid, open_orders=open_orders
        )

    # ---- Task 9: queue reads ---------------------------------------------
    def _conn(self):
        return tenant_conn(self._pool, tenant_uuid=self._uuid)

    @staticmethod
    def _parse(row) -> tuple[Recommendation, object, TaskStatus, float]:
        from trax_io_spine.guardrail.contracts import GuardrailOutcome

        rec = Recommendation.model_validate(row[0])
        outcome = GuardrailOutcome.model_validate(row[1])
        return rec, outcome, TaskStatus(row[2]), float(row[3])

    def _where(self, *, status, tier, type_, aog_min):
        clauses, params = ["tenant_id = %s::uuid", "status = %s"], [self._uuid, status.value]
        if tier is not None:
            clauses.append("tier = %s")
            params.append(int(tier))
        if type_ is not None:
            clauses.append("rec_type = %s")
            params.append(str(type_))
        if aog_min is not None:
            clauses.append("aog_level >= %s")
            params.append(int(aog_min))
        return " and ".join(clauses), params

    def _select(self, conn, *, status, sort_by, sort_dir, tier, type_, aog_min,
                limit=None, offset=None):
        where, params = self._where(status=status, tier=tier, type_=type_, aog_min=aog_min)
        direction = "desc" if sort_dir == "desc" else "asc"
        sql = (
            f"select {_ROW_COLS} from recommendations where {where} "  # noqa: S608
            f"order by {_SORT_COLS[sort_by]} {direction}, rec_id asc"
        )
        if limit is not None:
            sql += " limit %s offset %s"
            params += [limit, offset or 0]
        return conn.execute(sql, params).fetchall()

    def _rows(self, raw) -> list[QueueRow]:
        return [row_view(*self._parse(r)[:3], self._parse(r)[3]) for r in raw]

    def queue(self, *, status: TaskStatus = TaskStatus.PENDING, limit: int = 50):
        with self._conn() as conn:
            raw = self._select(
                conn, status=status, sort_by=QueueSortKey.PRIORITY, sort_dir="desc",
                tier=None, type_=None, aog_min=None, limit=limit, offset=0,
            )
            return self._rows(raw)

    def list_queue_page(
        self, *, status: TaskStatus = TaskStatus.PENDING, limit: int = 50, offset: int = 0,
        sort_by: QueueSortKey = QueueSortKey.PRIORITY, sort_dir: str = "desc",
        tier=None, type_=None, aog_min=None,
    ) -> tuple[list[QueueRow], int]:
        with self._conn() as conn:
            where, params = self._where(
                status=status, tier=tier, type_=type_, aog_min=aog_min
            )
            total = conn.execute(
                f"select count(*) from recommendations where {where}",  # noqa: S608
                params,
            ).fetchone()[0]
            raw = self._select(
                conn, status=status, sort_by=sort_by, sort_dir=sort_dir,
                tier=tier, type_=type_, aog_min=aog_min, limit=limit, offset=offset,
            )
            return self._rows(raw), total

    def list_queue_all(
        self, *, status: TaskStatus = TaskStatus.PENDING,
        sort_by: QueueSortKey = QueueSortKey.PRIORITY, sort_dir: str = "desc",
        tier=None, type_=None, aog_min=None,
    ) -> list[QueueRow]:
        with self._conn() as conn:
            raw = self._select(
                conn, status=status, sort_by=sort_by, sort_dir=sort_dir,
                tier=tier, type_=type_, aog_min=aog_min,
            )
            return self._rows(raw)

    def detail(self, rec_id: str) -> RecommendationDetail:
        with self._conn() as conn:
            row = conn.execute(
                "select rec, outcome, status from recommendations "
                "where tenant_id = %s::uuid and rec_id = %s",
                (self._uuid, rec_id),
            ).fetchone()
        if row is None:
            raise RecommendationNotFound(rec_id)
        rec = Recommendation.model_validate(row[0])
        from trax_io_spine.guardrail.contracts import GuardrailOutcome

        return detail_view(rec, GuardrailOutcome.model_validate(row[1]), TaskStatus(row[2]))
```

Implementer notes: (a) `_rows` calls `_parse` twice per row as written — fix to parse once (`parsed = [self._parse(r) for r in raw]`); it's written this way here to keep the plan diff-readable, tidy it in implementation. (b) Confirm the real import paths for `Recommendation` / `GuardrailOutcome` by looking at `bff/store.py`'s own imports and reuse those exact paths. (c) `str(type_)`/`str(rec.type)` must serialize identically on write (seed) and read (filter) — whatever `RecommendationType` serializes to in `rec_type` at seed time is what the filter compares against; if it's a `str`-enum, use `type_.value` in BOTH places.

- [ ] **Step 5: Run → pass; whole suite; ruff; commit**

Run: `uv run --extra dev --extra bff --extra bvr --extra pg pytest tests/pg tests/bff -q && uv run --extra dev ruff check .`
Expected: all PASS (queue parity across 8 sort combos + filters), ruff clean.

```bash
git add services/agent-spine/src/trax_io_spine/pg/store.py services/agent-spine/src/trax_io_spine/bff/store.py services/agent-spine/tests/pg/test_pg_store_queue.py
git commit -m "feat(pg): C1 Task 9 — PgPlannerStore queue reads with in-memory parity tests"
```

---

### Task 10: `PgPlannerStore` decisions — approve / reject / defer / bulk / kill switch

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/pg/store.py`
- Test: `services/agent-spine/tests/pg/test_pg_store_actions.py`

**Interfaces:**
- Consumes: Task 9 scaffold; `to_writeback_request` + `KillSwitchEngaged` + `ActionResult`, `RejectReason`, `BulkApproveFilter` — import them exactly as `bff/store.py` does.
- Produces (exact `PlannerStore` signatures, `bff/store.py:337-401`): `set_kill_switch(engaged)`, `kill_switch` (read-only property), `approve(rec_id) -> ActionResult`, `reject(rec_id, reason, detail="")`, `defer(rec_id, until=None)`, `bulk_approve(filter) -> tuple[int, list[ActionResult]]`, `history(*, pn, location)`, `rollback(req)`.
- Invariants: every decision action (1) updates the `recommendations` row status + decided fields, (2) inserts an append-only `decisions` row, (3) deletes the tenant's `bvr_cache` row (invalidation — same trigger set as the in-memory `_bvr_cache = None`: approve/reject/defer/bulk/rollback/kill-switch). Approve on `policy is None` raises `ValueError`; approve/bulk with kill switch engaged raises `KillSwitchEngaged` BEFORE any write.

- [ ] **Step 1: Write the failing tests**

`services/agent-spine/tests/pg/test_pg_store_actions.py`:

```python
"""Decision-lifecycle parity + durability (fresh store instance sees decisions)."""
from datetime import UTC, datetime
from pathlib import Path

import pytest
from trax_io_spine.bff.models import BulkApproveFilter, RejectReason, TaskStatus
from trax_io_spine.bff.store import KillSwitchEngaged, PlannerStore

from trax_io_spine.pg.seed import seed_store
from trax_io_spine.pg.store import PgPlannerStore

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
    report = seed_store(admin_pool, store=mem, slug="acme", name="Acme Air")
    pg = PgPlannerStore(pg_pool, tenant_slug="acme", tenant_uuid=report.tenant_uuid)
    return mem, pg, report


def _first_approvable(store):
    return next(r.recommendation_id for r in store.queue() if r.approvable)


def test_approve_parity_and_durability(stores, pg_pool):
    mem, pg, report = stores
    rid = _first_approvable(mem)
    m = mem.approve(rid)
    p = pg.approve(rid)
    assert (p.status, p.writeback.status) == (m.status, m.writeback.status)
    # durability: a FRESH store instance (new process, same DB) sees the decision
    fresh = PgPlannerStore(pg_pool, tenant_slug="acme", tenant_uuid=report.tenant_uuid)
    assert fresh.detail(rid).status is TaskStatus.APPROVED
    assert fresh.history(pn=p.writeback.pn, location=p.writeback.location)


def test_reject_and_defer(stores):
    _, pg, _ = stores
    rids = [r.recommendation_id for r in pg.queue()]
    r = pg.reject(rids[0], RejectReason.DATA_QUALITY, "bad demand rows")
    d = pg.defer(rids[1] if len(rids) > 1 else rids[0])
    assert r.status is TaskStatus.REJECTED and d.status is TaskStatus.DEFERRED


def test_bulk_approve_parity(stores):
    mem, pg, _ = stores
    f = BulkApproveFilter(tiers=None, max_delta_pct=None, criticality_min=None, types=None)
    m_count, _ = mem.bulk_approve(f)
    p_count, _ = pg.bulk_approve(f)
    assert p_count == m_count


def test_kill_switch_blocks_and_persists(stores, pg_pool):
    _, pg, report = stores
    pg.set_kill_switch(True)
    assert pg.kill_switch is True
    with pytest.raises(KillSwitchEngaged):
        pg.approve(_first_approvable(pg))
    fresh = PgPlannerStore(pg_pool, tenant_slug="acme", tenant_uuid=report.tenant_uuid)
    assert fresh.kill_switch is True
    pg.set_kill_switch(False)


def test_decisions_are_recorded(stores, admin_pool):
    _, pg, report = stores
    pg.reject(pg.queue()[0].recommendation_id, RejectReason.DATA_QUALITY)
    with admin_pool.connection() as conn:
        n = conn.execute(
            "select count(*) from decisions where tenant_id = %s::uuid and action='reject'",
            (report.tenant_uuid,),
        ).fetchone()[0]
        assert n >= 1
```

Note: check `RejectReason`'s real members in `bff/models.py` — if `DATA_QUALITY` isn't one, substitute an actual member in both tests; the parity contract is the enum itself, not this plan's guess.

- [ ] **Step 2: Run to verify fail → implement**

Add to `pg/store.py` (Task 10 section) — semantics copied from `bff/store.py:337-401`:

```python
    # ---- Task 10: decisions ----------------------------------------------
    @property
    def kill_switch(self) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "select engaged from kill_switches where tenant_id = %s::uuid",
                (self._uuid,),
            ).fetchone()
            return bool(row and row[0])

    def _decision(self, conn, *, rec_id, action, payload=None, principal="planner"):
        conn.execute(
            "insert into decisions (tenant_id, rec_id, action, payload, principal)"
            " values (%s::uuid, %s, %s, %s, %s)",
            (self._uuid, rec_id, action, json.dumps(payload or {}), principal),
        )
        conn.execute("delete from bvr_cache where tenant_id = %s::uuid", (self._uuid,))

    def _load_entry(self, conn, rec_id):
        row = conn.execute(
            "select rec, outcome, status from recommendations "
            "where tenant_id = %s::uuid and rec_id = %s for update",
            (self._uuid, rec_id),
        ).fetchone()
        if row is None:
            raise RecommendationNotFound(rec_id)
        from trax_io_spine.guardrail.contracts import GuardrailOutcome

        return (Recommendation.model_validate(row[0]),
                GuardrailOutcome.model_validate(row[1]), TaskStatus(row[2]))

    def _set_status(self, conn, rec_id, status: TaskStatus, **extra):
        sets, params = ["status = %s", "decided_at = now()"], [status.value]
        for col, val in extra.items():
            sets.append(f"{col} = %s")
            params.append(val)
        params += [self._uuid, rec_id]
        conn.execute(
            f"update recommendations set {', '.join(sets)} "  # noqa: S608
            "where tenant_id = %s::uuid and rec_id = %s",
            params,
        )

    def approve(self, rec_id: str) -> ActionResult:
        if self.kill_switch:
            raise KillSwitchEngaged(self.tenant_id)
        with self._conn() as conn:
            rec, outcome, _ = self._load_entry(conn, rec_id)
            if rec.policy is None:
                raise ValueError(f"recommendation {rec_id} has no writable policy")
            self._set_status(conn, rec_id, TaskStatus.APPROVED)
            self._decision(conn, rec_id=rec_id, action="approve")
        idem = (f"{rec.tenant_id}:{rec.part_number}:{rec.current_location}:"
                f"{rec.input_snapshot_hash}")
        result = self.writeback.write(
            to_writeback_request(rec, idempotency_key=idem, tier=outcome.tier)
        )
        return ActionResult(
            recommendation_id=rec_id, status=TaskStatus.APPROVED, writeback=result,
            message=f"written ({result.status.value})",
        )

    def reject(self, rec_id: str, reason: RejectReason, detail: str = "") -> ActionResult:
        with self._conn() as conn:
            self._load_entry(conn, rec_id)
            self._set_status(
                conn, rec_id, TaskStatus.REJECTED,
                reject_reason=reason.value, reject_detail=detail,
            )
            self._decision(conn, rec_id=rec_id, action="reject",
                           payload={"reason": reason.value, "detail": detail})
        return ActionResult(
            recommendation_id=rec_id, status=TaskStatus.REJECTED, message=reason.value
        )

    def defer(self, rec_id: str, until=None) -> ActionResult:
        with self._conn() as conn:
            self._load_entry(conn, rec_id)
            self._set_status(conn, rec_id, TaskStatus.DEFERRED, deferred_until=until)
            self._decision(conn, rec_id=rec_id, action="defer")
        return ActionResult(
            recommendation_id=rec_id, status=TaskStatus.DEFERRED, message="deferred"
        )

    def bulk_approve(self, filter: BulkApproveFilter) -> tuple[int, list[ActionResult]]:
        if self.kill_switch:
            raise KillSwitchEngaged(self.tenant_id)
        from trax_io_spine.guardrail.contracts import GuardrailOutcome

        with self._conn() as conn:
            raw = conn.execute(
                "select rec_id, rec, outcome from recommendations "
                "where tenant_id = %s::uuid and status = 'pending' and approvable",
                (self._uuid,),
            ).fetchall()
        targets = []
        for rec_id, rec_j, out_j in raw:
            rec = Recommendation.model_validate(rec_j)
            outcome = GuardrailOutcome.model_validate(out_j)
            if filter.tiers is not None and outcome.tier not in filter.tiers:
                continue
            if (filter.max_delta_pct is not None
                    and outcome.delta_pct > filter.max_delta_pct):
                continue
            if (filter.criticality_min is not None
                    and rec.criticality_tier < filter.criticality_min):
                continue
            if filter.types is not None and rec.type not in filter.types:
                continue
            targets.append(rec_id)
        results = [self.approve(rid) for rid in targets]
        return len(results), results

    def set_kill_switch(self, engaged: bool) -> None:
        with self._conn() as conn:
            conn.execute(
                "insert into kill_switches (tenant_id, engaged, updated_at)"
                " values (%s::uuid, %s, now()) on conflict (tenant_id)"
                " do update set engaged = excluded.engaged, updated_at = now()",
                (self._uuid, engaged),
            )
            self._decision(conn, rec_id=None, action="kill_switch",
                           payload={"engaged": engaged})

    def history(self, *, pn: str, location: str):
        return self.writeback.get_history(tenant_id=self.tenant_id, pn=pn, location=location)

    def rollback(self, req):
        result = self.writeback.rollback(req)
        with self._conn() as conn:
            self._decision(conn, rec_id=None, action="rollback",
                           payload={"pn": req.pn, "location": req.location,
                                    "status": result.status.value})
        return result
```

Add the imports Task 10 needs to the top of `pg/store.py`: `ActionResult`, `BulkApproveFilter`, `RejectReason` from `trax_io_spine.bff.models`; `KillSwitchEngaged` from `trax_io_spine.bff.store`; `to_writeback_request` from wherever `bff/store.py` imports it (copy that import line verbatim).

Ordering note (deliberate, document in the commit): `approve()` commits the status flip + decision row in one transaction, then performs the writeback — mirroring the in-memory sequence where a writeback exception leaves the entry pending is NOT possible with two systems without 2PC; C1 accepts status-first (the ledger's idempotency key makes a retried approve safe). The `fresh.history(...)` assertion in the durability test proves the write landed.

- [ ] **Step 3: Run → pass; ruff; commit**

Run: `uv run --extra dev --extra bff --extra bvr --extra pg pytest tests/pg -q && uv run --extra dev ruff check .`

```bash
git add services/agent-spine/src/trax_io_spine/pg/store.py services/agent-spine/tests/pg/test_pg_store_actions.py
git commit -m "feat(pg): C1 Task 10 — decision lifecycle (approve/reject/defer/bulk/kill switch) on Postgres"
```

---

### Task 11: `PgPlannerStore` seeded-view reads

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/pg/store.py`
- Test: `services/agent-spine/tests/pg/test_pg_store_views.py`

**Interfaces:**
- Produces (exact signatures from `bff/store.py`): `part_context(pn, location) -> PartContext`, `dashboard() -> DashboardSummary`, `forecast_summary() -> ForecastSummary`, `feeds_summary() -> FeedsSummary`.
- `dashboard()` = seeded `dashboard_static` payload with the two live fields recomputed by SQL: `open_recommendations` (count of `status='pending'`) and `net_cost_impact` (sum of `cost_impact` over `status='pending'`). **Before implementing, read `bff/store.py:663-750` and confirm those are exactly the fields derived from entry statuses — if the in-memory formula includes more (e.g. deferred), match it; the parity test is the arbiter.**
- Unknown part key: raise the same exception type the in-memory `part_context` raises for an unknown key (discover it in the parity test with `pytest.raises`; expected `KeyError`-family from its internal lookups — pin whatever it actually is).

- [ ] **Step 1: Write the failing tests**

`services/agent-spine/tests/pg/test_pg_store_views.py`:

```python
"""Seeded-view parity: pg reads == in-memory computes, before AND after decisions."""
from datetime import UTC, datetime
from pathlib import Path

import pytest
from trax_io_spine.bff.store import PlannerStore

from trax_io_spine.pg.seed import seed_store
from trax_io_spine.pg.store import PgPlannerStore

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
    report = seed_store(admin_pool, store=mem, slug="acme", name="Acme Air")
    return mem, PgPlannerStore(pg_pool, tenant_slug="acme", tenant_uuid=report.tenant_uuid)


def test_part_context_parity(stores):
    mem, pg = stores
    ks = mem._key_stats()[0]
    assert pg.part_context(ks.pn, ks.location) == mem.part_context(ks.pn, ks.location)


def test_part_context_unknown_key_matches_memory(stores):
    mem, pg = stores
    with pytest.raises(Exception) as mem_exc:
        mem.part_context("NOPE", "ZZZ")
    with pytest.raises(mem_exc.type):
        pg.part_context("NOPE", "ZZZ")


def test_dashboard_parity_incl_live_fields(stores):
    mem, pg = stores
    assert pg.dashboard() == mem.dashboard()
    rid = next(r.recommendation_id for r in mem.queue() if r.approvable)
    mem.approve(rid)
    pg.approve(rid)
    assert pg.dashboard() == mem.dashboard()


def test_forecast_and_feeds_parity(stores):
    mem, pg = stores
    assert pg.forecast_summary() == mem.forecast_summary()
    assert pg.feeds_summary() == mem.feeds_summary()
```

- [ ] **Step 2: Implement**

Add to `pg/store.py` (Task 11 section):

```python
    # ---- Task 11: seeded-view reads --------------------------------------
    def _snapshot(self, conn, kind: str) -> dict:
        row = conn.execute(
            "select payload from tenant_snapshots "
            "where tenant_id = %s::uuid and kind = %s",
            (self._uuid, kind),
        ).fetchone()
        if row is None:
            raise LookupError(f"tenant {self.tenant_id}: no seeded snapshot {kind!r}")
        return row[0]

    def part_context(self, pn: str, location: str) -> PartContext:
        with self._conn() as conn:
            row = conn.execute(
                "select context from part_contexts "
                "where tenant_id = %s::uuid and pn = %s and location = %s",
                (self._uuid, pn, location),
            ).fetchone()
        if row is None:
            raise KeyError((pn, location))  # adjust to match in-memory (see test)
        return PartContext.model_validate(row[0])

    def dashboard(self) -> DashboardSummary:
        with self._conn() as conn:
            static = self._snapshot(conn, "dashboard_static")
            live = conn.execute(
                "select count(*), coalesce(sum(cost_impact), 0) from recommendations "
                "where tenant_id = %s::uuid and status = 'pending'",
                (self._uuid,),
            ).fetchone()
        return DashboardSummary.model_validate(static).model_copy(
            update={"open_recommendations": int(live[0]),
                    "net_cost_impact": float(live[1])}
        )

    def forecast_summary(self) -> ForecastSummary:
        with self._conn() as conn:
            return ForecastSummary.model_validate(self._snapshot(conn, "forecast_summary"))

    def feeds_summary(self) -> FeedsSummary:
        with self._conn() as conn:
            return FeedsSummary.model_validate(self._snapshot(conn, "feeds_summary"))
```

Add imports: `PartContext`, `DashboardSummary`, `ForecastSummary`, `FeedsSummary` from `trax_io_spine.bff.models` (or `bff/feeds.py` for `FeedsSummary` — copy `app.py`'s import).

- [ ] **Step 3: Run → pass; ruff; commit**

Run: `uv run --extra dev --extra bff --extra bvr --extra pg pytest tests/pg/test_pg_store_views.py -v && uv run --extra dev ruff check .`

```bash
git add services/agent-spine/src/trax_io_spine/pg/store.py services/agent-spine/tests/pg/test_pg_store_views.py
git commit -m "feat(pg): C1 Task 11 — seeded-view reads (part context, dashboard live-merge, forecast, feeds)"
```

---

### Task 12: `PgPlannerStore` scenarios + BVR

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/pg/store.py`
- Modify: `services/agent-spine/src/trax_io_spine/bff/store.py` (ONLY if `_result_wire` needs `self` — extract a module-level `result_wire(params, result)` mirroring the Task 9 refactor pattern)
- Test: `services/agent-spine/tests/pg/test_pg_store_scenarios_bvr.py`

**Interfaces:**
- Produces (exact signatures from `bff/store.py:1046-1105` and `623`): `solve_scenario(params) -> ScenarioSolveResult`, `save_scenario(...)` (copy the exact signature from the file), `list_scenarios() -> list[Scenario]`, `get_scenario(scenario_id)`, `delete_scenario(scenario_id)`, `commit_scenario(scenario_id) -> ScenarioAuditEvent`, `scenario_audit_log() -> list[ScenarioAuditEvent]`, `bvr() -> BvrReport`.
- Scenario compute path: `_key_stats()` loads `part_keys.key_stats` rows → `KeyStats` objects → delegate to the SAME scenario-module functions the in-memory store uses (`PlannerStore._to_solver_params` / `_outcome_wire` are staticmethods — call them directly; if `_result_wire` uses instance state, extract `result_wire` first). Saved scenarios persist as `scenarios.payload` (`Scenario.model_dump(mode="json")`); audit events append to `scenario_audit`. `ScenarioNotFound` for unknown ids.
- `bvr()`: same input assembly as `bff/store.py:623-662`, sourced from Postgres — `key_facts` from `part_keys` + the `current_policies` snapshot (`policies["<pn>|<loc>"]` → baseline dict, `pol.rop` default 0 when absent); `rec_states` from all `recommendations` rows; `ledger=self.writeback.iter_history(...)`; `kill_switch=self.kill_switch`; `keys_total_portfolio` + `extract_date` from the `current_policies` snapshot payload. Result cached in `bvr_cache` (`report` JSONB); cache hit returns `BvrReport.model_validate(report)`; every Task-10 decision already invalidates.

- [ ] **Step 1: Write the failing tests**

`services/agent-spine/tests/pg/test_pg_store_scenarios_bvr.py`:

```python
"""Scenario + BVR parity. BVR compared minus generated_at (wall-clock differs)."""
from datetime import UTC, datetime
from pathlib import Path

import pytest
from trax_io_spine.bff.store import PlannerStore, ScenarioNotFound

from trax_io_spine.pg.seed import seed_store
from trax_io_spine.pg.store import PgPlannerStore

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
    report = seed_store(admin_pool, store=mem, slug="acme", name="Acme Air")
    return mem, PgPlannerStore(pg_pool, tenant_slug="acme", tenant_uuid=report.tenant_uuid)


def _bvr_dict(store):
    d = store.bvr().model_dump(mode="json")
    d.pop("generated_at", None)
    return d


def test_bvr_parity_and_invalidation(stores):
    mem, pg = stores
    assert _bvr_dict(pg) == _bvr_dict(mem)
    rid = next(r.recommendation_id for r in mem.queue() if r.approvable)
    mem.approve(rid)
    pg.approve(rid)
    assert _bvr_dict(pg) == _bvr_dict(mem)


def test_bvr_cache_round_trip(stores, admin_pool):
    _, pg = stores
    first = _bvr_dict(pg)   # computes + caches
    second = _bvr_dict(pg)  # must come from bvr_cache
    assert first == second


def test_scenario_solve_and_lifecycle(stores):
    mem, pg = stores
    # copy the params literal from any existing tests/bff/test_scenario.py solve test
    from tests.bff.test_scenario import DEFAULT_PARAMS  # reuse the suite's fixture params

    m = mem.solve_scenario(DEFAULT_PARAMS)
    p = pg.solve_scenario(DEFAULT_PARAMS)
    assert p.model_dump(mode="json") == m.model_dump(mode="json")

    saved = pg.save_scenario(name="test", params=DEFAULT_PARAMS)
    assert pg.get_scenario(saved.scenario_id).name == "test"
    assert len(pg.list_scenarios()) == 1
    pg.delete_scenario(saved.scenario_id)
    with pytest.raises(ScenarioNotFound):
        pg.get_scenario(saved.scenario_id)
```

If `tests/bff/test_scenario.py` exposes no importable `DEFAULT_PARAMS`, lift its literal params-construction into this file verbatim; if `save_scenario`'s signature differs from `(name, params)`, copy the real one from `bff/store.py:1052` into both the call and the implementation.

- [ ] **Step 2: Implement**

Add to `pg/store.py` (Task 12 section):

```python
    # ---- Task 12: scenarios + BVR ----------------------------------------
    def _key_stats(self) -> list:
        from trax_io_spine.bff.scenario import KeyStats

        with self._conn() as conn:
            rows = conn.execute(
                "select key_stats from part_keys where tenant_id = %s::uuid "
                "order by pn, location",
                (self._uuid,),
            ).fetchall()
        return [KeyStats.model_validate(r[0]) for r in rows]

    def bvr(self):
        from datetime import UTC, datetime

        from trax_io_spine.bvr import BvrReport, KeyFacts, RecState, build_bvr_report

        with self._conn() as conn:
            cached = conn.execute(
                "select report from bvr_cache where tenant_id = %s::uuid", (self._uuid,)
            ).fetchone()
            if cached is not None:
                return BvrReport.model_validate(cached[0])
            meta = self._snapshot(conn, "current_policies")
            raw = conn.execute(
                "select rec, status from recommendations where tenant_id = %s::uuid",
                (self._uuid,),
            ).fetchall()
        policies = meta["policies"]
        key_facts, policy_of = [], {}
        for ks in self._key_stats():
            pol = policies.get(f"{ks.pn}|{ks.location}")
            policy_of[(ks.pn, ks.location)] = pol
            key_facts.append(KeyFacts(
                pn=ks.pn, location=ks.location, criticality_tier=ks.criticality_tier,
                rop=pol["rop"] if pol else 0, mean_per_day=ks.mean_per_day,
                lead_mean=ks.lead_mean,
                unit_cost=ks.unit_cost if ks.unit_cost > 0 else None,
            ))
        rec_states = [
            RecState(rec=Recommendation.model_validate(r), status=s) for r, s in raw
        ]

        def baseline_for(entry):
            return policy_of.get((entry.pn, entry.location))

        report = build_bvr_report(
            tenant_id=self.tenant_id, extract_date=meta.get("extract_date"),
            generated_at=datetime.now(UTC), key_facts=key_facts, rec_states=rec_states,
            ledger=self.writeback.iter_history(self.tenant_id),
            baseline_for=baseline_for, kill_switch=self.kill_switch,
            keys_total_portfolio=meta["keys_total"],
        )
        with self._conn() as conn:
            conn.execute(
                "insert into bvr_cache (tenant_id, report) values (%s::uuid, %s) "
                "on conflict (tenant_id) do update set report = excluded.report,"
                " computed_at = now()",
                (self._uuid, json.dumps(report.model_dump(mode="json"))),
            )
        return report
```

For the scenario methods: copy the bodies of `solve_scenario` / `save_scenario` / `list_scenarios` / `get_scenario` / `delete_scenario` / `commit_scenario` / `scenario_audit_log` from `bff/store.py:1046-1105`, replacing `self._scenarios` dict access with `scenarios`-table SQL (insert on save with `payload=_scenario.model_dump(mode='json')`; select-order-by-created on list; delete on delete; `ScenarioNotFound(scenario_id)` when a select returns None) and `self._audit_log.append(event)` with a `scenario_audit` insert + `scenario_audit_log` reading events back in `at` order. Solver delegation (`PlannerStore._to_solver_params(...)`, `_outcome_wire`, `result_wire`) stays identical. IMPORTANT: exact bodies come from the file — the imports at the top of `bff/store.py` name every scenario type; copy them.

Correction from the in-memory `bvr()`: the memory version passes `pol.rop`/attribute access on a policy OBJECT; the pg version stores plain dicts at seed time, so `baseline_for` returns the dict directly (same shape the in-memory `baseline_for` builds by hand) and `KeyFacts.rop` reads `pol["rop"]`. The parity test proves equivalence.

- [ ] **Step 3: Run → pass; ruff; commit**

Run: `uv run --extra dev --extra bff --extra bvr --extra pg pytest tests/pg -q && uv run --extra dev ruff check .`

```bash
git add services/agent-spine/src/trax_io_spine/pg/store.py services/agent-spine/src/trax_io_spine/bff/store.py services/agent-spine/tests/pg/test_pg_store_scenarios_bvr.py
git commit -m "feat(pg): C1 Task 12 — scenarios + BVR on Postgres with in-memory parity"
```

---

### Task 13: App integration + asgi Pg boot mode + scale gate

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/asgi.py`
- Test: `services/agent-spine/tests/pg/test_app_pg.py`
- Test: `services/agent-spine/tests/pg/test_pg_scale.py`

**Interfaces:**
- Produces: `create_planner_app({"acme": pg_store})` serving the full existing OpenAPI contract off Postgres (duck typing — zero `app.py` changes; that's the point of the seam).
- Produces: asgi boot mode — when `DATABASE_URL` is set, `asgi.py` builds a pool, resolves `PLANNER_TENANT`'s uuid via `resolve_tenant_uuid`, constructs `PgPlannerStore`, and serves; existing `PLANNER_SNAPSHOT_DIR`/`EXTRACT_DIR` paths unchanged when `DATABASE_URL` is absent. Precedence: `DATABASE_URL` > `PLANNER_SNAPSHOT_DIR` > `PLANNER_RECS_FILE` > `EXTRACT_DIR`.
- Produces: an env-gated scale test (`PG_BENCH_SNAPSHOT_DIR` — point it at `deploy/_local_extract/emro_net_full_snapshot` locally): seeds the full network dataset and asserts `list_queue_page` < 1.0s and `dashboard()` < 1.0s (the spec §12 benchmark gate). Skips clean when unset.

- [ ] **Step 1: Write the app-level test**

`services/agent-spine/tests/pg/test_app_pg.py`:

```python
"""The existing FastAPI contract served off PgPlannerStore — key routes only
(the exhaustive route behavior suite is tests/bff/test_app.py; parity of the
store beneath it is Tasks 9-12. This pins the duck-typing seam end-to-end)."""
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.store import PlannerStore

from trax_io_spine.pg.seed import seed_store
from trax_io_spine.pg.store import PgPlannerStore

EXTRACT = (
    Path(__file__).resolve().parents[3]
    / "recommendation-engine" / "examples" / "extract_sample"
)


@pytest.fixture()
def client(admin_pool, pg_pool):
    mem = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(EXTRACT),
        now=datetime(2026, 4, 1, tzinfo=UTC),
    )
    report = seed_store(admin_pool, store=mem, slug="acme", name="Acme Air")
    store = PgPlannerStore(pg_pool, tenant_slug="acme", tenant_uuid=report.tenant_uuid)
    return TestClient(create_planner_app({"acme": store}))


def test_queue_and_detail(client):
    rows = client.get("/v1/tenants/acme/recommendations").json()
    assert rows and client.get(
        f"/v1/tenants/acme/recommendations/{rows[0]['recommendation_id']}"
    ).status_code == 200


def test_approve_flow_and_history(client):
    rows = client.get("/v1/tenants/acme/recommendations").json()
    rid = next(r["recommendation_id"] for r in rows if r["approvable"])
    r = client.post(f"/v1/tenants/acme/recommendations/{rid}/approve")
    assert r.status_code == 200
    row = next(x for x in rows if x["recommendation_id"] == rid)
    h = client.get(f"/v1/tenants/acme/parts/{row['pn']}/{row['location']}/history")
    assert h.status_code == 200 and h.json()


def test_kill_switch_423(client):
    client.put("/v1/tenants/acme/killswitch", json={"engaged": True})
    rows = client.get("/v1/tenants/acme/recommendations").json()
    rid = next(r["recommendation_id"] for r in rows if r["approvable"])
    assert client.post(
        f"/v1/tenants/acme/recommendations/{rid}/approve"
    ).status_code == 423


def test_dashboard_bvr_unknown_tenant(client):
    assert client.get("/v1/tenants/acme/dashboard").status_code == 200
    assert client.get("/v1/tenants/acme/reports/bvr").status_code == 200
    assert client.get("/v1/tenants/ghost/dashboard").status_code == 404
```

Before finalizing, open `tests/bff/test_app.py` and copy its EXACT route paths/verbs for approve/killswitch/history — if any differ from the guesses above (e.g. kill-switch verb is POST, or history lives under a different segment), use the suite's real paths.

- [ ] **Step 2: asgi Pg mode**

In `bff/asgi.py`, add the branch ABOVE the existing snapshot-dir logic (keep everything else untouched, extend the module docstring's env table):

```python
database_url = os.environ.get("DATABASE_URL")
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
    app = create_planner_app({tenant: store})
else:
    ...  # existing snapshot/extract logic, unchanged
```

(Adapt variable names to the file's actual structure — `tenant` already exists at line ~35; the final `app = create_planner_app(...)` call site is the thing being branched.)

- [ ] **Step 3: Scale gate**

`services/agent-spine/tests/pg/test_pg_scale.py`:

```python
"""Spec §12 benchmark gate: full-network seed must serve interactive reads <1s.
Env-gated (needs the gitignored 58.9K-key snapshot); skips clean otherwise."""
import os
import time

import pytest

SNAPSHOT = os.environ.get("PG_BENCH_SNAPSHOT_DIR")


@pytest.mark.skipif(not SNAPSHOT, reason="PG_BENCH_SNAPSHOT_DIR not set")
def test_full_network_read_latency(admin_pool, pg_pool):
    from trax_io_spine.pg.seed import seed_tenant
    from trax_io_spine.pg.store import PgPlannerStore

    report = seed_tenant(admin_pool, slug="bench", name="Bench", snapshot_dir=SNAPSHOT)
    store = PgPlannerStore(pg_pool, tenant_slug="bench", tenant_uuid=report.tenant_uuid)
    t0 = time.perf_counter()
    rows, total = store.list_queue_page(limit=50)
    t1 = time.perf_counter()
    store.dashboard()
    t2 = time.perf_counter()
    assert rows and total > 10_000
    assert t1 - t0 < 1.0, f"queue page took {t1 - t0:.2f}s"
    assert t2 - t1 < 1.0, f"dashboard took {t2 - t1:.2f}s"
```

- [ ] **Step 4: Run → pass; ruff; commit**

Run: `uv run --extra dev --extra bff --extra bvr --extra pg pytest tests/pg -q && uv run --extra dev --extra bff --extra bvr pytest -q && uv run --extra dev ruff check .`
Expected: pg suite green, whole existing suite untouched-green, ruff clean. Locally, also run the scale gate once: `PG_BENCH_SNAPSHOT_DIR=$PWD/../../deploy/_local_extract/emro_net_full_snapshot uv run --extra dev --extra bff --extra bvr --extra pg pytest tests/pg/test_pg_scale.py -v` and record the timings in the commit message.

```bash
git add services/agent-spine/src/trax_io_spine/bff/asgi.py services/agent-spine/tests/pg/test_app_pg.py services/agent-spine/tests/pg/test_pg_scale.py
git commit -m "feat(pg): C1 Task 13 — app served off Postgres, DATABASE_URL boot mode, 59K scale gate"
```

---

### Task 14: Bookkeeping

**Files:**
- Modify: `ROADMAP.md` — add a "Commercial SaaS track (C1–C4)" section: C1 `[x]` with today's date + the four C1 deliverables (schema+RLS, claims hook, PgWritebackTarget/PgPlannerStore, DATABASE_URL boot mode); C2/C3/C4 `[ ]` rows pointing at the spec.
- Modify: `TASKS.md` — dated session entry: what C1 shipped, test counts (before/after), the status-first approve-ordering deviation, the scale-gate timings.
- Modify: `CLAUDE.md` Section A — repo layout gains `supabase/` (migrations + README); the agent-spine test row gains `--extra pg` (Docker required, skips clean without); new CLI `trax-io-pg-seed`; one line describing the Pg boot mode env (`DATABASE_URL` precedence over `PLANNER_SNAPSHOT_DIR`).
- Modify: `docs/superpowers/specs/2026-07-20-commercialization-architecture-design.md` — mark the C1 row "shipped (plan: this file's sibling)".

- [ ] **Step 1: Make all four edits** (follow each file's existing entry format exactly — dated, tables preserved)
- [ ] **Step 2: Commit**

```bash
git add ROADMAP.md TASKS.md CLAUDE.md docs/superpowers/specs/2026-07-20-commercialization-architecture-design.md
git commit -m "docs: C1 Supabase-foundation bookkeeping — ROADMAP C-track, TASKS, CLAUDE.md pg commands"
```
