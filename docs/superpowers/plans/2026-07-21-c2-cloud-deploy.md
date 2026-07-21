# C2 — Cloud Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aeronta Inventory served from the cloud — BFF + idle worker on Railway against the live `aeronta-inventory` Supabase database, `apps/web` on Vercel with a Supabase auth shell (login, JWT-verified BFF, full user management).

**Architecture:** Spec Approach A — supabase-js authenticates in the browser (email/password); every `/v1` call carries the Supabase JWT through a Vercel rewrite to the Railway BFF, which verifies it against the project's **ES256 JWKS** (confirmed live: `https://sluoxufnqwusmtckklnv.supabase.co/auth/v1/.well-known/jwks.json`, kid `2c004e4f-e473-4685-a8ff-a8331c86a910`) and impersonates the verified tenant per-request via the existing `tenant_conn` as `trax_app`. Migration 0006 adds the security-definer slug resolve (retiring the bypassrls boot workaround), memberships write policies, the `jobs` table, and the ledger idempotency index.

**Tech Stack:** Python ≥3.12 (`pyjwt[crypto]` for ES256), FastAPI (existing BFF), psycopg3, supabase-js v2 + React 18/Vite (existing `apps/web`), Railway CLI, Vercel CLI, Supabase CLI (`db push` owns the live schema).

**Spec:** [docs/superpowers/specs/2026-07-21-c2-cloud-deploy-design.md](../specs/2026-07-21-c2-cloud-deploy-design.md)

## Global Constraints

- Live facts (from C1/C2 provisioning — reuse verbatim): Supabase ref `sluoxufnqwusmtckklnv`; pooler host `aws-0-us-east-1.pooler.supabase.com:5432`, user format `<role>.<ref>` (direct DB host is IPv6-only — never use it); demo tenant `aeronta-demo` uuid `753b64bd-9885-4639-b116-8f2c5c497232`; Vercel project `aeronta-inventory` (`prj_WQlrbadCxnWfLQOCteebIIJENzFz`); secrets ONLY in gitignored `deploy/_local_extract/aeronta-supabase.env` (contains `AERONTA_SUPABASE_DB_PASSWORD`, `AERONTA_TRAX_APP_PASSWORD`, `AERONTA_TRAX_SEED_PASSWORD`) — never in the repo, image, or plan reports.
- The live database schema is owned EXCLUSIVELY by `supabase db push --db-url <pooler-url>` (one-runner rule). The Python `apply_migrations` runner is for the test harness only.
- JWT claims contract (C1, unchanged): custom claims `tenant_id` (uuid text) + `tenant_role` (`owner|admin|planner|viewer`).
- `ruff check` clean — the only allowed pre-existing findings are 2× B905 in `tests/bff/test_csv_export.py`. Backend suites: `uv run --extra dev --extra bff --extra bvr --extra pg-test pytest -q` from `services/agent-spine` (after Task 2 renames the test extra) must stay green incl. the 71-test pg suite (Docker required, skips clean without). Frontend: `cd apps/web && npm test` (288+ Vitest) + `npm run build` + `npm run lint` green.
- Test-isolation convention (session-established): pg-harness fixtures seed UNIQUE Postgres slugs per test file (`acme-c2t<N>`), never plain `acme`.
- New env vars introduced by C2 (names locked here): `AUTH_JWKS_URL`, `AUTH_JWT_SECRET` (HS256 fallback, optional), `AUTH_AUDIENCE` (default `authenticated`), `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (BFF/members only), `WORKER_DATABASE_URL`, `WORKER_POLL_SECONDS` (default `5`), frontend `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_API_BASE` (default same-origin ``).
- Never touch `oracle19c`/MySQL containers. Repo path contains a space — quote paths in every shell command.
- Commit after every task with the task's exact commit message; each commit leaves all suites green.
- User gates (cannot be automated; the executing controller must pause and ask): Railway CLI `railway login` (Task 9); everything else is scriptable with existing CLI auth (supabase, vercel already logged in).

---

### Task 1: Migration 0006 — slug resolve, memberships writes, jobs, idempotency index

**Files:**
- Create: `supabase/migrations/20260721000006_c2_auth_jobs.sql`
- Test: `services/agent-spine/tests/pg/test_c2_schema.py`

**Interfaces:**
- Produces: `public.resolve_tenant_slug(p_slug text) returns uuid` — SECURITY DEFINER (owner: migration runner = postgres on live, superuser on harness), `stable`, executable by `trax_app`; returns NULL for unknown slugs.
- Produces: `memberships` INSERT/UPDATE/DELETE policies + grants for `trax_app`, gated on claims `tenant_role in ('admin','owner')` AND `tenant_id` match (SELECT policy from C1 unchanged).
- Produces: `public.jobs` table — `id bigint generated always as identity primary key, tenant_id uuid not null references tenants(id) on delete cascade, kind text not null check (kind in ('ingest','recompute','bvr')), status text not null default 'queued' check (status in ('queued','running','done','failed','dead')), payload jsonb not null default '{}'::jsonb, attempts integer not null default 0, claimed_at timestamptz, finished_at timestamptz, error text, created_at timestamptz not null default now()`; index `jobs_claim_idx (status, id)`; RLS: select+insert for `trax_app` (tenant-scoped); ALL for `trax_seed`.
- Produces: expression index `writeback_ledger_idem_idx on writeback_ledger (tenant_id, (entry->>'idempotency_key'))`.

- [ ] **Step 1: Write the migration**

`supabase/migrations/20260721000006_c2_auth_jobs.sql`:

```sql
-- C2: auth bootstrap + user management writes + jobs + ledger idempotency index.

-- (1) Slug->uuid resolve usable by trax_app BEFORE tenant claims exist (the BFF's
-- boot/request path). SECURITY DEFINER: runs as the migration owner (postgres on
-- live Supabase), which sees public.tenants regardless of RLS. Retires the
-- "DATABASE_URL must be a bypassrls role" workaround from C1 Task 13.
create function public.resolve_tenant_slug(p_slug text) returns uuid
language sql stable security definer
set search_path = public
as $$
  select id from public.tenants where slug = p_slug
$$;
revoke all on function public.resolve_tenant_slug(text) from public;
grant execute on function public.resolve_tenant_slug(text) to trax_app, trax_seed;

-- (2) Memberships writes for user management (C1 left trax_app read-only by design).
-- Admin/owner of the CURRENT tenant may manage that tenant's memberships only.
create policy memberships_insert on public.memberships for insert to trax_app
  with check (
    tenant_id = (select public.current_tenant_id())
    and (select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
      ->> 'tenant_role') in ('admin', 'owner')
  );
create policy memberships_update on public.memberships for update to trax_app
  using (
    tenant_id = (select public.current_tenant_id())
    and (select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
      ->> 'tenant_role') in ('admin', 'owner')
  )
  with check (tenant_id = (select public.current_tenant_id()));
create policy memberships_delete on public.memberships for delete to trax_app
  using (
    tenant_id = (select public.current_tenant_id())
    and (select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
      ->> 'tenant_role') in ('admin', 'owner')
  );
grant insert, update, delete on public.memberships to trax_app;

-- (3) Jobs queue (spec §5): C2 ships the table + an idle worker; C3 registers handlers.
create table public.jobs (
  id         bigint generated always as identity primary key,
  tenant_id  uuid not null references public.tenants (id) on delete cascade,
  kind       text not null check (kind in ('ingest', 'recompute', 'bvr')),
  status     text not null default 'queued'
             check (status in ('queued', 'running', 'done', 'failed', 'dead')),
  payload    jsonb not null default '{}'::jsonb,
  attempts   integer not null default 0,
  claimed_at timestamptz,
  finished_at timestamptz,
  error      text,
  created_at timestamptz not null default now()
);
create index jobs_claim_idx on public.jobs (status, id);
create index jobs_tenant_idx on public.jobs (tenant_id, created_at desc);

alter table public.jobs enable row level security;
create policy jobs_select on public.jobs for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy jobs_insert on public.jobs for insert to trax_app
  with check (tenant_id = (select public.current_tenant_id()));
grant select, insert on public.jobs to trax_app;
grant all on public.jobs to trax_seed;
grant usage, select on all sequences in schema public to trax_app;

-- (4) Ledger idempotency-key expression index (C1 final-review pre-flight #2):
-- PgWritebackTarget._replay filters on entry->>'idempotency_key' per write.
create index writeback_ledger_idem_idx
  on public.writeback_ledger (tenant_id, (entry->>'idempotency_key'));

-- (5) Tenant switching: a fresh token mint carries no "requested tenant" claim,
-- so the hook needs a stored preference. Users write ONLY their own row (RLS on
-- the JWT sub); membership validity is enforced by the hook itself at mint time.
create table public.tenant_preferences (
  user_id   uuid primary key,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  updated_at timestamptz not null default now()
);
alter table public.tenant_preferences enable row level security;
create policy tenant_preferences_own on public.tenant_preferences for all to trax_app
  using (user_id = (select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb ->> 'sub')::uuid)
  with check (user_id = (select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb ->> 'sub')::uuid);
grant select, insert, update, delete on public.tenant_preferences to trax_app;
grant all on public.tenant_preferences to trax_seed;

-- (6) Hook v2: preference-aware selection. Priority: explicit requested claim
-- (legacy path, still validated) > stored preference (validated) > most-recent
-- membership. Foreign/stale preferences fall back — never pass through.
create or replace function public.custom_access_token_hook(event jsonb) returns jsonb
language plpgsql stable as $$
declare
  uid uuid := public.try_uuid(event->>'user_id');
  requested uuid := public.try_uuid(nullif(event->'claims'->>'tenant_id', ''));
  preferred uuid;
  m record;
begin
  if uid is null then
    return jsonb_set(event, '{claims}', (event->'claims') - 'tenant_id' - 'tenant_role');
  end if;
  select tenant_id into preferred from public.tenant_preferences where user_id = uid;
  select tenant_id, role into m
  from public.memberships
  where user_id = uid
  order by (tenant_id = requested) desc nulls last,
           (tenant_id = preferred) desc nulls last,
           created_at desc
  limit 1;
  if m is null then
    return jsonb_set(event, '{claims}', (event->'claims') - 'tenant_id' - 'tenant_role');
  end if;
  return jsonb_set(
    event, '{claims}',
    (event->'claims')
      || jsonb_build_object('tenant_id', m.tenant_id::text, 'tenant_role', m.role)
  );
end;
$$;
grant execute on function public.custom_access_token_hook(jsonb) to trax_seed;
```

Hook-v2 note: the two-SELECT structure of the C1 hook collapses into ONE ordered select here (the Task 3 C1 reviewer's own simplification suggestion) — the ordering expression alone implements requested > preferred > most-recent, and foreign values simply order false. The four existing C1 hook tests in `tests/pg/test_claims_hook.py` must stay green unmodified (they pin requested/fallback/strip semantics); add to the NEW `test_c2_schema.py`:

```python
def test_hook_honors_stored_preference(admin_pool):
    import json as _json

    u = "00000000-0000-0000-0000-00000000c2aa"
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into memberships (user_id, tenant_id, role, created_at) values "
            "(%s, %s, 'planner', now() - interval '2 days'), "
            "(%s, %s, 'admin', now() - interval '1 day') on conflict do nothing",
            (u, A, u, B),
        )
        conn.execute(
            "insert into tenant_preferences (user_id, tenant_id) values (%s, %s) "
            "on conflict (user_id) do update set tenant_id = excluded.tenant_id",
            (u, A),
        )
        row = conn.execute(
            "select public.custom_access_token_hook(%s::jsonb)",
            (_json.dumps({"user_id": u, "claims": {"sub": u}}),),
        ).fetchone()
        claims = row[0]["claims"]
        assert claims["tenant_id"] == A  # preference beats most-recent (B)


def test_preferences_rls_own_row_only(pg_pool):
    import json as _json

    me = "00000000-0000-0000-0000-00000000c2ab"
    other = "00000000-0000-0000-0000-00000000c2ac"
    with pg_pool.connection() as conn:
        conn.execute(
            "select set_config('request.jwt.claims', %s, true)",
            (_json.dumps({"sub": me, "tenant_id": A, "tenant_role": "planner"}),),
        )
        conn.execute(
            "insert into tenant_preferences (user_id, tenant_id) values (%s::uuid, %s)",
            (me, A),
        )
        import psycopg
        import pytest as _pytest

        with _pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "insert into tenant_preferences (user_id, tenant_id) values (%s::uuid, %s)",
                (other, A),
            )
```

- [ ] **Step 2: Write the failing tests**

`services/agent-spine/tests/pg/test_c2_schema.py`:

```python
"""Migration 0006 semantics: slug resolve as trax_app, memberships write gates,
jobs isolation. Slugs here: acme-c2t1 / globex-c2t1 (session isolation convention)."""
import json

import psycopg
import pytest
from tests.pg.conftest import as_tenant

A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaa0c21"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbb0c21"
U_NEW = "00000000-0000-0000-0000-00000000c210"


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values "
            "(%s, 'acme-c2t1', 'A'), (%s, 'globex-c2t1', 'B') on conflict (id) do nothing",
            (A, B),
        )
        conn.commit()


def _claims(conn, tenant, role):
    as_tenant(conn, tenant, role=role)


def test_resolve_tenant_slug_as_trax_app_without_claims(pg_pool):
    with pg_pool.connection() as conn:
        row = conn.execute("select public.resolve_tenant_slug('acme-c2t1')::text").fetchone()
        assert row[0] == A
        assert conn.execute(
            "select public.resolve_tenant_slug('nope-c2t1')"
        ).fetchone()[0] is None


def test_admin_can_insert_membership_planner_cannot(pg_pool):
    with pg_pool.connection() as conn:
        _claims(conn, A, "admin")
        conn.execute(
            "insert into memberships (user_id, tenant_id, role) values (%s, %s, 'planner')",
            (U_NEW, A),
        )
        conn.commit()
    with pg_pool.connection() as conn:
        _claims(conn, A, "planner")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "insert into memberships (user_id, tenant_id, role) values "
                "('00000000-0000-0000-0000-00000000c211', %s, 'viewer')",
                (A,),
            )


def test_admin_cannot_write_foreign_tenant_membership(pg_pool):
    with pg_pool.connection() as conn:
        _claims(conn, A, "admin")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "insert into memberships (user_id, tenant_id, role) values (%s, %s, 'viewer')",
                (U_NEW, B),
            )


def test_admin_update_and_delete_membership(pg_pool):
    with pg_pool.connection() as conn:
        _claims(conn, A, "owner")
        conn.execute(
            "update memberships set role = 'viewer' where user_id = %s and tenant_id = %s",
            (U_NEW, A),
        )
        conn.execute(
            "delete from memberships where user_id = %s and tenant_id = %s", (U_NEW, A)
        )
        conn.commit()


def test_jobs_tenant_isolated_and_app_cannot_update(pg_pool, admin_pool):
    with pg_pool.connection() as conn:
        _claims(conn, A, "planner")
        conn.execute(
            "insert into jobs (tenant_id, kind, payload) values (%s, 'recompute', '{}')",
            (A,),
        )
        conn.commit()
    with pg_pool.connection() as conn:
        _claims(conn, B, "planner")
        assert conn.execute("select count(*) from jobs").fetchone()[0] == 0
    with pg_pool.connection() as conn:
        _claims(conn, A, "planner")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("update jobs set status = 'done'")


def test_idem_index_exists(admin_pool):
    with admin_pool.connection() as conn:
        assert conn.execute(
            "select 1 from pg_indexes where indexname = 'writeback_ledger_idem_idx'"
        ).fetchone()
```

- [ ] **Step 3: Run → pass locally; ruff**

Run: `cd services/agent-spine && uv run --extra dev --extra pg pytest tests/pg/test_c2_schema.py -v && uv run --extra dev ruff check .`
Expected: 6 PASS (session fixture auto-applies 0006); ruff = only the 2 pre-existing B905. Honesty check: temporarily change `security definer` to `security invoker` in a scratch copy → `test_resolve_tenant_slug_as_trax_app_without_claims` must FAIL (RLS hides the row) → restore.

- [ ] **Step 4: Push to LIVE**

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer" && set -a && source deploy/_local_extract/aeronta-supabase.env && set +a && supabase db push --db-url "postgresql://postgres.sluoxufnqwusmtckklnv:${AERONTA_SUPABASE_DB_PASSWORD}@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
```
Expected output includes `Applying migration 20260721000006_c2_auth_jobs.sql...` then `Finished supabase db push.` Then verify live: run the one-liner `select public.resolve_tenant_slug('aeronta-demo')::text` as `trax_app` via a psycopg one-liner (pattern: Task 9 Step 3) — expect `753b64bd-9885-4639-b116-8f2c5c497232`.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20260721000006_c2_auth_jobs.sql services/agent-spine/tests/pg/test_c2_schema.py
git commit -m "feat(pg): C2 Task 1 — migration 0006 (slug resolve, memberships writes, jobs, idem index)"
```

---

### Task 2: Hardening — bvr single-transaction cache + pg/pg-test extra split

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/pg/store.py` (the `bvr()` method and `_key_stats()`)
- Modify: `services/agent-spine/pyproject.toml` (extras)
- Modify: `deploy/bff.Dockerfile` (add `--extra pg` to the `uv sync` line)
- Modify: `services/agent-spine/tests/pg/conftest.py` + any test-file comments referencing `--extra pg` (commands become `--extra pg-test`)
- Test: existing `tests/pg/test_pg_store_scenarios_bvr.py` (green = the refactor is invisible)

**Interfaces:**
- Consumes: `PgPlannerStore.bvr()` and `_key_stats()` as shipped in C1 Task 12 (cache check → snapshot meta → recs; separate connections for key stats / ledger / kill switch / cache write).
- Produces: same public signatures, but ALL reads + the cache upsert happen inside ONE `tenant_conn` transaction (closes the C1 final-review stale-serve window). `_key_stats()` gains an optional `conn=None` parameter (`def _key_stats(self, conn=None)`) — when given, it reuses the open connection; bare calls behave as before (scenario paths unchanged).
- Produces: pyproject extras — `pg = ["psycopg[binary,pool]>=3.2"]` and `pg-test = ["psycopg[binary,pool]>=3.2", "testcontainers[postgres]>=4.7"]`.

- [ ] **Step 1: Split the extras**

In `services/agent-spine/pyproject.toml` `[project.optional-dependencies]` replace the `pg` line with:

```toml
pg = ["psycopg[binary,pool]>=3.2"]
pg-test = ["psycopg[binary,pool]>=3.2", "testcontainers[postgres]>=4.7"]
```

Sweep test commands: `grep -rn '"--extra pg"\|--extra pg\b' services/agent-spine/tests deploy docs/superpowers/plans/2026-07-21-c2-cloud-deploy.md CLAUDE.md` and update harness-facing invocations to `--extra pg-test` (deploy-facing stay `--extra pg`). CLAUDE.md's agent-spine row: pg suite command becomes `--extra pg-test`.

- [ ] **Step 2: Add pg to the Dockerfile**

In `deploy/bff.Dockerfile`, change the sync line to:

```dockerfile
RUN uv sync --extra bff --extra bvr --extra pdf --extra pg --no-dev && uv pip install uvicorn
```

(Comment above it gains: `+ pg (psycopg — DATABASE_URL boot mode; lean after the pg/pg-test split)`.)

- [ ] **Step 3: Refactor `bvr()` to one transaction**

In `pg/store.py`: change `_key_stats` to accept an optional open connection, and make `bvr()` do cache-check, meta read, recs read, key stats, ledger enumeration, kill-switch read, report build, and cache upsert inside a single `with self._conn() as conn:` block. The ledger read inlines the same SQL `PgWritebackTarget.iter_history` uses (ordered `pn, location, version`, `HistoryEntry.model_validate`) but on THIS connection; the kill-switch read likewise (`select engaged from kill_switches where tenant_id = %s::uuid`). The `build_bvr_report(...)` call site and all inputs stay byte-identical. Structure:

```python
    def _key_stats(self, conn=None) -> list:
        from trax_io_spine.bff.scenario import KeyStats

        def _load(c):
            rows = c.execute(
                "select key_stats from part_keys where tenant_id = %s::uuid "
                "order by pn, location",
                (self._uuid,),
            ).fetchall()
            return [KeyStats(**r[0]) for r in rows]

        if conn is not None:
            return _load(conn)
        with self._conn() as c:
            return _load(c)
```

and in `bvr()` (single-transaction shape — adapt names to the file's existing Task 12 code, keeping the report-assembly block verbatim):

```python
    def bvr(self):
        from datetime import UTC, datetime

        from trax_io_spine.bvr.models import BvrReport, KeyFacts, RecState
        from trax_io_spine.bvr.report import build_bvr_report
        from trax_io_spine.contracts import HistoryEntry

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
            key_stats = self._key_stats(conn)
            ledger = tuple(
                HistoryEntry.model_validate(r[0])
                for r in conn.execute(
                    "select entry from writeback_ledger where tenant_id = %s::uuid "
                    "order by pn, location, version",
                    (self._uuid,),
                ).fetchall()
            )
            ks_row = conn.execute(
                "select engaged from kill_switches where tenant_id = %s::uuid",
                (self._uuid,),
            ).fetchone()
            kill_switch = bool(ks_row and ks_row[0])
            # ---- report assembly: keep the existing Task 12 block verbatim,
            # substituting the locals above for the old per-connection fetches ----
            ...
            conn.execute(
                "insert into bvr_cache (tenant_id, report) values (%s::uuid, %s) "
                "on conflict (tenant_id) do update set report = excluded.report,"
                " computed_at = now()",
                (self._uuid, json.dumps(report.model_dump(mode="json"))),
            )
        return report
```

(The `...` marks the existing, already-reviewed assembly lines from C1 Task 12 — key_facts/policy_of loop, `RecState` list, `baseline_for`, `build_bvr_report(...)` — which move inside the block unchanged. The implementer copies them from the current file; they are not new code.)

- [ ] **Step 4: Run the full suite (extra renamed) + ruff; commit**

Run: `cd services/agent-spine && uv sync --extra dev --extra bff --extra bvr --extra pg-test && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest -q && uv run --extra dev ruff check .`
Expected: whole suite green (365+/2 plus the 6 Task-1 tests — bvr parity/cache tests prove the refactor invisible); ruff unchanged.

```bash
git add services/agent-spine/pyproject.toml services/agent-spine/uv.lock deploy/bff.Dockerfile services/agent-spine/src/trax_io_spine/pg/store.py services/agent-spine/tests CLAUDE.md
git commit -m "feat(pg): C2 Task 2 — bvr single-transaction cache + pg/pg-test extra split + Dockerfile pg"
```

---

### Task 3: BFF JWT verification middleware

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/bff/auth.py`
- Modify: `services/agent-spine/pyproject.toml` (`bff` extra gains `"pyjwt[crypto]>=2.8"`)
- Modify: `services/agent-spine/src/trax_io_spine/bff/app.py` (`create_planner_app` signature + middleware registration ONLY — no route changes)
- Modify: `services/agent-spine/src/trax_io_spine/bff/asgi.py` (build verifier from env; pass `tenant_uuids`; switch slug resolve to `resolve_tenant_slug`; update the docstring's DATABASE_URL role note — trax_app now works)
- Test: `services/agent-spine/tests/bff/test_auth_middleware.py`

**Interfaces:**
- Produces (`bff/auth.py`):
  - `class TokenVerifier(Protocol): def verify(self, token: str) -> dict: ...` — returns claims dict; raises `InvalidTokenError` (re-export `jwt.InvalidTokenError`) on any failure.
  - `class JwksVerifier: def __init__(self, jwks_url: str, *, audience: str = "authenticated")` — PyJWT `PyJWKClient(jwks_url)` (cached), `jwt.decode(token, key, algorithms=["ES256", "RS256"], audience=audience)`.
  - `class HsVerifier: def __init__(self, secret: str, *, audience: str = "authenticated")` — `algorithms=["HS256"]`.
  - `def build_verifier_from_env() -> TokenVerifier | None` — `AUTH_JWKS_URL` → JwksVerifier; else `AUTH_JWT_SECRET` → HsVerifier; else None + `logging.getLogger("trax_io_spine.bff.auth").warning("AUTH DISABLED — trusted path-param mode (dev only)")`.
  - `class AuthMiddleware` (pure ASGI or Starlette `BaseHTTPMiddleware`): skips when verifier is None; on `/v1/tenants/{slug}/...` paths extracts `Authorization: Bearer` → 401 JSON `{"detail": "missing or invalid token"}` when absent/invalid; verified claims must contain `tenant_id`; when a `tenant_uuids` mapping is provided and has the path slug, mismatch → 403 `{"detail": "tenant mismatch"}`; stashes `scope["state"]["claims"] = claims` (role available to Task 4's routes as `claims.get("tenant_role", "viewer")`). Non-`/v1` paths (docs, health) pass through.
- Produces (`app.py`): `create_planner_app(stores, *, verifier=None, tenant_uuids=None, admin_api=None, members_stores=None)` — new keyword-only params, all defaulting to None (every existing caller/test unchanged); registers `AuthMiddleware` when `verifier` is not None. (`admin_api`/`members_stores` are consumed in Task 4 — declare them now so the signature is stable.)
- Produces (`asgi.py` DATABASE_URL branch): resolves the tenant uuid via `select public.resolve_tenant_slug(%s)` (works as `trax_app` after migration 0006), builds `verifier = build_verifier_from_env()`, passes `tenant_uuids={tenant: tenant_uuid}`.

- [ ] **Step 1: Write the failing tests**

`services/agent-spine/tests/bff/test_auth_middleware.py`:

```python
"""JWT middleware: 401/403/dev-mode semantics against a real ES256 keypair.

No network: JwksVerifier is exercised via a monkeypatched PyJWKClient whose
signing key is generated in-test (cryptography is a pyjwt[crypto] dependency).
"""
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, generate_private_key
from fastapi.testclient import TestClient
from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier, JwksVerifier, build_verifier_from_env

TENANT_UUID = "753b64bd-9885-4639-b116-8f2c5c497232"


class _StaticVerifier:
    """TokenVerifier double for app-level tests: real HsVerifier below covers crypto."""

    def __init__(self, secret="s3cret"):
        self._v = HsVerifier(secret)

    def verify(self, token):
        return self._v.verify(token)


def _token(secret="s3cret", *, tenant=TENANT_UUID, role="planner", exp_min=5, aud="authenticated"):
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": "u1", "aud": aud, "iat": now, "exp": now + timedelta(minutes=exp_min),
         "tenant_id": tenant, "tenant_role": role},
        secret, algorithm="HS256",
    )


@pytest.fixture()
def client(store_factory):
    app = create_planner_app(
        {"aeronta-demo": store_factory()},
        verifier=_StaticVerifier(),
        tenant_uuids={"aeronta-demo": TENANT_UUID},
    )
    return TestClient(app)


def test_missing_token_401(client):
    assert client.get("/v1/tenants/aeronta-demo/recommendations").status_code == 401


def test_garbage_token_401(client):
    r = client.get(
        "/v1/tenants/aeronta-demo/recommendations",
        headers={"Authorization": "Bearer not.a.jwt"},
    )
    assert r.status_code == 401


def test_expired_token_401(client):
    r = client.get(
        "/v1/tenants/aeronta-demo/recommendations",
        headers={"Authorization": f"Bearer {_token(exp_min=-5)}"},
    )
    assert r.status_code == 401


def test_wrong_tenant_403(client):
    r = client.get(
        "/v1/tenants/aeronta-demo/recommendations",
        headers={"Authorization": f"Bearer {_token(tenant='99999999-9999-9999-9999-999999999999')}"},
    )
    assert r.status_code == 403


def test_valid_token_200(client):
    r = client.get(
        "/v1/tenants/aeronta-demo/recommendations",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert r.status_code == 200


def test_no_verifier_passthrough(store_factory):
    app = create_planner_app({"aeronta-demo": store_factory()})
    assert TestClient(app).get(
        "/v1/tenants/aeronta-demo/recommendations"
    ).status_code == 200


def test_build_verifier_from_env(monkeypatch, caplog):
    monkeypatch.delenv("AUTH_JWKS_URL", raising=False)
    monkeypatch.delenv("AUTH_JWT_SECRET", raising=False)
    import logging

    with caplog.at_level(logging.WARNING):
        assert build_verifier_from_env() is None
    assert any("AUTH DISABLED" in r.message for r in caplog.records)
    monkeypatch.setenv("AUTH_JWT_SECRET", "x")
    assert isinstance(build_verifier_from_env(), HsVerifier)


def test_jwks_verifier_es256(monkeypatch):
    key = generate_private_key(SECP256R1())
    tok = jwt.encode(
        {"sub": "u1", "aud": "authenticated", "tenant_id": TENANT_UUID,
         "exp": datetime.now(UTC) + timedelta(minutes=5)},
        key, algorithm="ES256", headers={"kid": "k1"},
    )
    v = JwksVerifier("https://example.invalid/jwks.json")

    class _FakeSigningKey:
        def __init__(self, k):
            self.key = k

    monkeypatch.setattr(
        v, "_signing_key_for", lambda token: _FakeSigningKey(key.public_key())
    )
    claims = v.verify(tok)
    assert claims["tenant_id"] == TENANT_UUID
    with pytest.raises(jwt.InvalidTokenError):
        v.verify(tok + "tamper")
```

`store_factory` fixture: add to this test file (NOT conftest) — builds the standard in-memory sample store the existing bff tests use; copy the construction from `tests/bff/test_app.py`'s fixture (extract-sample + fixed `now`), wrapped in a function so each test gets a fresh store.

- [ ] **Step 2: Run to verify fail**

Run: `cd services/agent-spine && uv sync --extra dev --extra bff --extra bvr --extra pg-test && uv run --extra dev --extra bff --extra bvr pytest tests/bff/test_auth_middleware.py -v`
Expected: FAIL — `ModuleNotFoundError: trax_io_spine.bff.auth` (after adding `pyjwt[crypto]>=2.8` to the `bff` extra so `jwt`/`cryptography` import).

- [ ] **Step 3: Implement `bff/auth.py`**

```python
"""JWT verification for the BFF (C2 spec §3).

Verifier absent => DEV MODE: trusted path-param behavior, loud boot warning.
Verifier present => every /v1/tenants/{slug}/* request needs a valid Supabase
JWT whose verified tenant_id claim matches the slug's tenant uuid.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Protocol

import jwt
from jwt import InvalidTokenError, PyJWKClient

log = logging.getLogger("trax_io_spine.bff.auth")


class TokenVerifier(Protocol):
    def verify(self, token: str) -> dict: ...


class HsVerifier:
    def __init__(self, secret: str, *, audience: str = "authenticated") -> None:
        self._secret = secret
        self._aud = audience

    def verify(self, token: str) -> dict:
        return jwt.decode(token, self._secret, algorithms=["HS256"], audience=self._aud)


class JwksVerifier:
    def __init__(self, jwks_url: str, *, audience: str = "authenticated") -> None:
        self._client = PyJWKClient(jwks_url)
        self._aud = audience

    def _signing_key_for(self, token: str):
        return self._client.get_signing_key_from_jwt(token)

    def verify(self, token: str) -> dict:
        key = self._signing_key_for(token)
        return jwt.decode(
            token, key.key, algorithms=["ES256", "RS256"], audience=self._aud
        )


def build_verifier_from_env() -> TokenVerifier | None:
    aud = os.environ.get("AUTH_AUDIENCE", "authenticated")
    jwks = os.environ.get("AUTH_JWKS_URL")
    if jwks:
        return JwksVerifier(jwks, audience=aud)
    secret = os.environ.get("AUTH_JWT_SECRET")
    if secret:
        return HsVerifier(secret, audience=aud)
    log.warning("AUTH DISABLED — trusted path-param mode (dev only)")
    return None


def _reject(status: int, detail: str):
    async def responder(scope, receive, send):
        body = json.dumps({"detail": detail}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})

    return responder


class AuthMiddleware:
    """Pure ASGI middleware — no route changes; claims land in scope['state']."""

    def __init__(self, app, *, verifier: TokenVerifier,
                 tenant_uuids: dict[str, str] | None = None) -> None:
        self.app = app
        self.verifier = verifier
        self.tenant_uuids = tenant_uuids or {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/v1/tenants/"):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return await _reject(401, "missing or invalid token")(scope, receive, send)
        try:
            claims = self.verifier.verify(auth[7:])
        except InvalidTokenError:
            return await _reject(401, "missing or invalid token")(scope, receive, send)
        if "tenant_id" not in claims:
            return await _reject(401, "missing or invalid token")(scope, receive, send)
        slug = scope["path"].split("/")[3]
        expected = self.tenant_uuids.get(slug)
        if expected is not None and claims["tenant_id"] != expected:
            return await _reject(403, "tenant mismatch")(scope, receive, send)
        scope.setdefault("state", {})["claims"] = claims
        return await self.app(scope, receive, send)
```

- [ ] **Step 4: Wire into `create_planner_app` and `asgi.py`**

`app.py`: extend the signature to `def create_planner_app(stores, *, verifier=None, tenant_uuids=None, admin_api=None, members_stores=None) -> FastAPI:` and, after the app object exists, add:

```python
    if verifier is not None:
        from trax_io_spine.bff.auth import AuthMiddleware

        app.add_middleware(AuthMiddleware, verifier=verifier, tenant_uuids=tenant_uuids)
```

(`add_middleware` with a pure-ASGI class: FastAPI passes kwargs through — verify with the test; if Starlette's signature fights, wrap via `app.middleware_stack` pattern: `app.add_middleware(...)` works for ASGI classes taking `app` first.) `admin_api`/`members_stores` are accepted and stored on `app.state` for Task 4: `app.state.admin_api = admin_api; app.state.members_stores = members_stores or {}`.

Also add to `app.py` (deploy health probe — outside `/v1`, so the middleware never guards it; Railway's health check hits it tokenless):

```python
    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "tenants": sorted(stores)}
```

Test (in `test_auth_middleware.py`): `assert client.get("/healthz").status_code == 200` on the verifier-enabled app, no token.

`asgi.py` DATABASE_URL branch: replace the `resolve_tenant_uuid` call with `conn.execute("select public.resolve_tenant_slug(%s)", (tenant,)).fetchone()`; add `verifier=build_verifier_from_env()` and `tenant_uuids={tenant: tenant_uuid}` to the `create_planner_app` call; update the docstring paragraph that says a bypassrls role is required — now: "any role with execute on resolve_tenant_slug works; use trax_app in production."

- [ ] **Step 5: Run → pass; suites; ruff; commit**

Run: `uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/bff tests/pg -q && uv run --extra dev ruff check .`
Expected: all green (existing tests untouched by the default-None params); ruff unchanged.

```bash
git add services/agent-spine/src/trax_io_spine/bff/auth.py services/agent-spine/src/trax_io_spine/bff/app.py services/agent-spine/src/trax_io_spine/bff/asgi.py services/agent-spine/pyproject.toml services/agent-spine/uv.lock services/agent-spine/tests/bff/test_auth_middleware.py
git commit -m "feat(bff): C2 Task 3 — JWT verification middleware (ES256 JWKS + HS256), dev-mode fallback"
```

---

### Task 4: Members management — store, Admin API seam, routes

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/pg/members.py`
- Create: `services/agent-spine/src/trax_io_spine/bff/members_routes.py`
- Modify: `services/agent-spine/src/trax_io_spine/bff/app.py` (include the router; ~3 lines)
- Test: `services/agent-spine/tests/pg/test_members.py`

**Interfaces:**
- Consumes: `tenant_conn` (role parameter carries the VERIFIED `tenant_role`), migration 0006 policies, `app.state.claims` from Task 3's middleware (`request.state.claims` in FastAPI handlers), `create_planner_app(..., admin_api=, members_stores=)` params from Task 3.
- Produces (`pg/members.py`):
  - `class LastOwnerError(Exception)` · `class MemberNotFound(Exception)`
  - `class MembershipStore: def __init__(self, pool, *, tenant_uuid: str)` with methods `list(self, *, role: str) -> list[dict]` (rows `{user_id, role, created_at}`), `add(self, *, user_id: str, member_role: str, role: str) -> None`, `update_role(self, *, user_id: str, member_role: str, role: str) -> None`, `remove(self, *, user_id: str, role: str) -> None` — `role` is ALWAYS the caller's verified claim role (drives RLS via `tenant_conn(..., role=role)`); `update_role`/`remove` raise `LastOwnerError` when the target is the last `owner` (check inside the same transaction: `select count(*) from memberships where tenant_id=%s::uuid and role='owner'`), `MemberNotFound` when the target row doesn't exist.
  - `class AdminApi(Protocol): def invite(self, email: str) -> str: ...` (returns new user_id) `; def emails_for(self, user_ids: list[str]) -> dict[str, str]: ...`
  - `class HttpxAdminApi: def __init__(self, supabase_url: str, service_key: str)` — `invite` POSTs `{supabase_url}/auth/v1/invite` (headers `apikey`/`Authorization: Bearer <service_key>`) returning `json()["id"]`; `emails_for` GETs `{supabase_url}/auth/v1/admin/users/{id}` per id (missing → omitted). Any non-2xx raises `AdminApiError(Exception)`.
- Produces (`bff/members_routes.py`): `router = APIRouter()` with routes `GET /v1/tenants/{tenant_id}/members`, `POST /v1/tenants/{tenant_id}/members/invite` (body `{"email": str, "role": "admin"|"planner"|"viewer"}`), `PATCH /v1/tenants/{tenant_id}/members/{user_id}` (body `{"role": ...}`), `DELETE /v1/tenants/{tenant_id}/members/{user_id}`. Gates read `request.state.claims` (populated by the middleware): no claims → 401 (auth disabled ⇒ these routes are unavailable: return 401 "auth required" — members management NEVER runs in dev-trusted mode); `tenant_role` not in `('admin','owner')` → 403; granting or revoking `owner` requires caller `owner` → 403. Status mapping: `LastOwnerError` → 409, `MemberNotFound` → 404, `AdminApiError` → 502 `{"detail": "identity provider error"}`, invite of an email whose user already holds a membership → 409. The store for a tenant comes from `app.state.members_stores[tenant_id]`; absent → 404.
- Registration in `app.py`: `app.include_router(members_router)` — the router is always mounted; its handlers self-gate on claims (401 when absent) so no store/dev-mode leakage.
- Produces (tenant switching, consumed by Task 7's switcher):
  - `trax_io_spine.pg.db.tenant_conn` gains an optional `sub: str | None = None` keyword (threaded into `tenant_claims(..., sub=sub)`; default behavior unchanged — every existing caller compiles).
  - `MembershipStore.set_preference(self, *, user_id: str, target_tenant_uuid: str, role: str) -> None` — upserts `tenant_preferences` under `tenant_conn(..., role=role, sub=user_id)` (RLS permits only the caller's own row; the hook validates membership at mint time, so a foreign tenant preference is inert).
  - Route `POST /v1/auth/activate-tenant` in `members_routes.py`, body `{"tenant_id": "<uuid>"}` — OUTSIDE `/v1/tenants/` so the middleware's tenant-match doesn't block cross-tenant switching; requires valid claims (401 otherwise; the middleware only guards `/v1/tenants/*`, so this handler verifies via `request.state.claims` presence — dev mode without verifier → 401); writes the caller's (`claims["sub"]`) preference via ANY configured members store's `set_preference` (they share the pool; use `next(iter(app.state.members_stores.values()))`, 503 if none configured); returns 204. Frontend flow: activate → `supabase.auth.refreshSession()` → reload.
  - Test additions to `tests/pg/test_members.py`: activate with planner token → 204 + `tenant_preferences` row visible via admin_pool; activate without token → 401.

- [ ] **Step 1: Write the failing tests**

`services/agent-spine/tests/pg/test_members.py`:

```python
"""MembershipStore semantics on the pg harness + members routes end-to-end with a
FakeAdminApi and the real middleware. Slug: acme-c2t4."""
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier

from trax_io_spine.pg.members import (
    AdminApiError,
    HttpxAdminApi,
    LastOwnerError,
    MembershipStore,
)

T_UUID = "cccccccc-cccc-cccc-cccc-cccccccc0c24"
OWNER = "00000000-0000-0000-0000-00000000c240"
PLANNER = "00000000-0000-0000-0000-00000000c241"


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, 'acme-c2t4', 'A') "
            "on conflict (id) do nothing",
            (T_UUID,),
        )
        conn.execute(
            "insert into memberships (user_id, tenant_id, role) values "
            "(%s, %s, 'owner'), (%s, %s, 'planner') on conflict do nothing",
            (OWNER, T_UUID, PLANNER, T_UUID),
        )
        conn.commit()


@pytest.fixture()
def store(pg_pool):
    return MembershipStore(pg_pool, tenant_uuid=T_UUID)


def test_list_add_update_remove_as_admin(store):
    new = "00000000-0000-0000-0000-00000000c242"
    store.add(user_id=new, member_role="viewer", role="admin")
    assert any(m["user_id"] == new for m in store.list(role="admin"))
    store.update_role(user_id=new, member_role="planner", role="admin")
    store.remove(user_id=new, role="admin")
    assert all(m["user_id"] != new for m in store.list(role="admin"))


def test_planner_role_cannot_write(store):
    import psycopg

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        store.add(
            user_id="00000000-0000-0000-0000-00000000c243",
            member_role="viewer", role="planner",
        )


def test_last_owner_guard(store):
    with pytest.raises(LastOwnerError):
        store.remove(user_id=OWNER, role="owner")
    with pytest.raises(LastOwnerError):
        store.update_role(user_id=OWNER, member_role="planner", role="owner")


class FakeAdminApi:
    def __init__(self):
        self.invited: list[str] = []

    def invite(self, email):
        self.invited.append(email)
        return f"00000000-0000-0000-0000-0000000{len(self.invited):05d}"

    def emails_for(self, user_ids):
        return {u: f"{u[-4:]}@example.com" for u in user_ids}


def _tok(role, tenant=T_UUID, secret="s3cret"):
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": OWNER, "aud": "authenticated", "iat": now,
         "exp": now + timedelta(minutes=5), "tenant_id": tenant, "tenant_role": role},
        secret, algorithm="HS256",
    )


@pytest.fixture()
def client(pg_pool):
    fake = FakeAdminApi()
    app = create_planner_app(
        {},  # planner stores not needed for members routes
        verifier=HsVerifier("s3cret"),
        tenant_uuids={"acme-c2t4": T_UUID},
        admin_api=fake,
        members_stores={"acme-c2t4": MembershipStore(pg_pool, tenant_uuid=T_UUID)},
    )
    return TestClient(app), fake


def test_members_routes_full_cycle(client):
    c, fake = client
    h_owner = {"Authorization": f"Bearer {_tok('owner')}"}
    h_planner = {"Authorization": f"Bearer {_tok('planner')}"}

    assert c.get("/v1/tenants/acme-c2t4/members", headers=h_planner).status_code == 403
    r = c.get("/v1/tenants/acme-c2t4/members", headers=h_owner)
    assert r.status_code == 200 and any(m["role"] == "owner" for m in r.json())

    r = c.post("/v1/tenants/acme-c2t4/members/invite", headers=h_owner,
               json={"email": "new@acme.test", "role": "planner"})
    assert r.status_code == 200 and fake.invited == ["new@acme.test"]
    uid = r.json()["user_id"]

    assert c.patch(f"/v1/tenants/acme-c2t4/members/{uid}", headers=h_planner,
                   json={"role": "viewer"}).status_code == 403
    assert c.patch(f"/v1/tenants/acme-c2t4/members/{uid}", headers=h_owner,
                   json={"role": "viewer"}).status_code == 200
    assert c.delete(f"/v1/tenants/acme-c2t4/members/{uid}", headers=h_owner).status_code == 200
    assert c.delete(f"/v1/tenants/acme-c2t4/members/{OWNER}", headers=h_owner).status_code == 409


def test_members_routes_require_auth(pg_pool):
    app = create_planner_app({})  # no verifier => members routes must refuse
    c = TestClient(app)
    assert c.get("/v1/tenants/acme-c2t4/members").status_code == 401


def test_httpx_admin_api_error_shape(monkeypatch):
    api = HttpxAdminApi("https://example.invalid", "svc")

    class _R:
        status_code = 500
        text = "boom"

        def json(self):
            return {}

    monkeypatch.setattr("httpx.post", lambda *a, **k: _R())
    with pytest.raises(AdminApiError):
        api.invite("x@y.z")
```

- [ ] **Step 2: Run to verify fail → implement**

Run: `uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_members.py -v`
Expected: FAIL — `ModuleNotFoundError: trax_io_spine.pg.members`.

Implement `pg/members.py` per the Interfaces block:

```python
"""Tenant membership management (C2 spec §4). RLS does the enforcement: every
call runs under tenant_conn with the CALLER'S verified role — a planner-role
claim cannot write memberships no matter what this code does."""
from __future__ import annotations

import httpx

from .db import tenant_conn


class LastOwnerError(Exception):
    pass


class MemberNotFound(Exception):
    pass


class AdminApiError(Exception):
    pass


class MembershipStore:
    def __init__(self, pool, *, tenant_uuid: str) -> None:
        self._pool = pool
        self._uuid = tenant_uuid

    def _conn(self, role: str):
        return tenant_conn(self._pool, tenant_uuid=self._uuid, role=role)

    def list(self, *, role: str) -> list[dict]:
        with self._conn(role) as conn:
            rows = conn.execute(
                "select user_id::text, role, created_at from memberships "
                "where tenant_id = %s::uuid order by created_at",
                (self._uuid,),
            ).fetchall()
            return [
                {"user_id": r[0], "role": r[1], "created_at": r[2].isoformat()}
                for r in rows
            ]

    def add(self, *, user_id: str, member_role: str, role: str) -> None:
        with self._conn(role) as conn:
            conn.execute(
                "insert into memberships (user_id, tenant_id, role) "
                "values (%s::uuid, %s::uuid, %s)",
                (user_id, self._uuid, member_role),
            )

    def _guard_last_owner(self, conn, user_id: str) -> None:
        target = conn.execute(
            "select role from memberships where tenant_id = %s::uuid "
            "and user_id = %s::uuid for update",
            (self._uuid, user_id),
        ).fetchone()
        if target is None:
            raise MemberNotFound(user_id)
        if target[0] == "owner":
            owners = conn.execute(
                "select count(*) from memberships "
                "where tenant_id = %s::uuid and role = 'owner'",
                (self._uuid,),
            ).fetchone()[0]
            if owners <= 1:
                raise LastOwnerError(user_id)

    def update_role(self, *, user_id: str, member_role: str, role: str) -> None:
        with self._conn(role) as conn:
            self._guard_last_owner(conn, user_id)
            conn.execute(
                "update memberships set role = %s "
                "where tenant_id = %s::uuid and user_id = %s::uuid",
                (member_role, self._uuid, user_id),
            )

    def remove(self, *, user_id: str, role: str) -> None:
        with self._conn(role) as conn:
            self._guard_last_owner(conn, user_id)
            conn.execute(
                "delete from memberships where tenant_id = %s::uuid and user_id = %s::uuid",
                (self._uuid, user_id),
            )


class HttpxAdminApi:
    def __init__(self, supabase_url: str, service_key: str) -> None:
        self._base = supabase_url.rstrip("/")
        self._headers = {
            "apikey": service_key, "Authorization": f"Bearer {service_key}"
        }

    def invite(self, email: str) -> str:
        r = httpx.post(
            f"{self._base}/auth/v1/invite", headers=self._headers,
            json={"email": email}, timeout=15,
        )
        if r.status_code >= 300:
            raise AdminApiError(r.status_code)
        return r.json()["id"]

    def emails_for(self, user_ids: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for uid in user_ids:
            r = httpx.get(
                f"{self._base}/auth/v1/admin/users/{uid}",
                headers=self._headers, timeout=15,
            )
            if r.status_code < 300:
                out[uid] = r.json().get("email", "")
        return out
```

Implement `bff/members_routes.py` (APIRouter; handlers read `request.state.claims`, catch the exceptions per the Interfaces status mapping; on invite: `user_id = admin_api.invite(email)` then `store.add(user_id=user_id, member_role=body.role, role=claims_role)`, response `{"user_id": user_id, "role": body.role}`; GET enriches rows with `emails_for`). `app.py`: `from trax_io_spine.bff.members_routes import router as members_router` + `app.include_router(members_router)` + the two `app.state` assignments from Task 3's Step 4.

Also wire `asgi.py`'s DATABASE_URL branch for deploy: when `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` env are present, pass `admin_api=HttpxAdminApi(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])` and always pass `members_stores={tenant: MembershipStore(pool, tenant_uuid=tenant_uuid)}` to `create_planner_app` (imports from `trax_io_spine.pg.members`); absent env → `admin_api=None` (invite route then 502s with "identity provider error" — acceptable, members listing still works).

- [ ] **Step 3: Run → pass; suites; ruff; commit**

Run: `uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg tests/bff -q && uv run --extra dev ruff check .`

```bash
git add services/agent-spine/src/trax_io_spine/pg/members.py services/agent-spine/src/trax_io_spine/bff/members_routes.py services/agent-spine/src/trax_io_spine/bff/app.py services/agent-spine/tests/pg/test_members.py
git commit -m "feat(bff): C2 Task 4 — members management (RLS-backed store, Admin API seam, gated routes)"
```

---

### Task 5: Worker — jobs poll loop

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/pg/worker.py`
- Test: `services/agent-spine/tests/pg/test_worker.py`

**Interfaces:**
- Consumes: `jobs` table (Task 1), `make_pool` (C1).
- Produces: `HANDLERS: dict[str, Callable[[dict], None]]` (module-level registry, EMPTY in C2 — C3 registers `ingest`/`recompute`); `claim_one(conn) -> tuple[int, str, str, dict] | None` (id, tenant_id, kind, payload — `update jobs set status='running', claimed_at=now(), attempts=attempts+1 where id = (select id from jobs where status='queued' order by id limit 1 for update skip locked) returning ...`); `run_once(pool) -> bool` (claimed & processed something?); `run_forever(database_url: str, poll_seconds: float)` with SIGTERM/SIGINT-driven clean exit + per-cycle heartbeat log; `main()` entrypoint reading `WORKER_DATABASE_URL` (fallback `DATABASE_URL`) + `WORKER_POLL_SECONDS` — `python -m trax_io_spine.pg.worker`.
- Semantics: unknown kind → status `dead`, error `no handler registered for kind '<kind>'`, finished_at set; handler raises → attempts < 3 ⇒ back to `queued` (error recorded), attempts ≥ 3 ⇒ `failed`; handler returns ⇒ `done`. All transitions in the claim transaction's connection, committed per job.

- [ ] **Step 1: Write the failing tests**

`services/agent-spine/tests/pg/test_worker.py`:

```python
"""Worker claim/dispatch semantics on the harness (admin pool = trax_seed-grade)."""
import pytest

from trax_io_spine.pg import worker as w

T = "dddddddd-dddd-dddd-dddd-dddddddd0c25"


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, 'acme-c2t5', 'A') "
            "on conflict (id) do nothing",
            (T,),
        )
        conn.commit()


def _enqueue(admin_pool, kind="recompute", payload="{}"):
    with admin_pool.connection() as conn:
        row = conn.execute(
            "insert into jobs (tenant_id, kind, payload) values (%s, %s, %s) returning id",
            (T, kind, payload),
        ).fetchone()
        conn.commit()
        return row[0]


def _status(admin_pool, jid):
    with admin_pool.connection() as conn:
        return conn.execute(
            "select status, attempts, error from jobs where id = %s", (jid,)
        ).fetchone()


def test_unknown_kind_goes_dead(admin_pool):
    jid = _enqueue(admin_pool)
    assert w.run_once(admin_pool) is True
    status, attempts, error = _status(admin_pool, jid)
    assert status == "dead" and "no handler registered" in error


def test_handler_success_marks_done(admin_pool, monkeypatch):
    seen = []
    monkeypatch.setitem(w.HANDLERS, "recompute", lambda payload: seen.append(payload))
    jid = _enqueue(admin_pool, payload='{"x": 1}')
    assert w.run_once(admin_pool) is True
    assert _status(admin_pool, jid)[0] == "done" and seen == [{"x": 1}]


def test_handler_failure_retries_then_fails(admin_pool, monkeypatch):
    def boom(payload):
        raise RuntimeError("kaput")

    monkeypatch.setitem(w.HANDLERS, "recompute", boom)
    jid = _enqueue(admin_pool)
    for expected_attempts in (1, 2, 3):
        assert w.run_once(admin_pool) is True
        status, attempts, error = _status(admin_pool, jid)
        assert attempts == expected_attempts and "kaput" in error
        assert status == ("failed" if expected_attempts == 3 else "queued")
    assert w.run_once(admin_pool) is False  # nothing left to claim


def test_empty_queue_returns_false(admin_pool):
    assert w.run_once(admin_pool) is False
```

- [ ] **Step 2: Run to verify fail → implement**

Run: `uv run --extra dev --extra pg-test pytest tests/pg/test_worker.py -v`
Expected: FAIL — no module.

`services/agent-spine/src/trax_io_spine/pg/worker.py`:

```python
"""Idle jobs worker (C2 spec §5): claims via FOR UPDATE SKIP LOCKED, dispatches
from HANDLERS (empty until C3), retries x3, dead-letters unknown kinds.
Run: python -m trax_io_spine.pg.worker  (env: WORKER_DATABASE_URL | DATABASE_URL,
WORKER_POLL_SECONDS default 5)."""
from __future__ import annotations

import logging
import os
import signal
import time
from collections.abc import Callable

from .db import make_pool

log = logging.getLogger("trax_io_spine.pg.worker")

HANDLERS: dict[str, Callable[[dict], None]] = {}
MAX_ATTEMPTS = 3

_CLAIM = """
update jobs set status = 'running', claimed_at = now(), attempts = attempts + 1
where id = (select id from jobs where status = 'queued'
            order by id limit 1 for update skip locked)
returning id, tenant_id::text, kind, payload, attempts
"""


def run_once(pool) -> bool:
    with pool.connection() as conn:
        row = conn.execute(_CLAIM).fetchone()
        if row is None:
            return False
        jid, _tenant, kind, payload, attempts = row
        handler = HANDLERS.get(kind)
        if handler is None:
            conn.execute(
                "update jobs set status = 'dead', finished_at = now(), error = %s "
                "where id = %s",
                (f"no handler registered for kind '{kind}'", jid),
            )
            return True
        try:
            handler(payload)
        except Exception as exc:  # noqa: BLE001 — the loop must survive any handler
            status = "failed" if attempts >= MAX_ATTEMPTS else "queued"
            conn.execute(
                "update jobs set status = %s, error = %s, "
                "finished_at = case when %s = 'failed' then now() end where id = %s",
                (status, f"{type(exc).__name__}: {exc}", status, jid),
            )
            return True
        conn.execute(
            "update jobs set status = 'done', finished_at = now(), error = null "
            "where id = %s",
            (jid,),
        )
        return True


def run_forever(database_url: str, poll_seconds: float) -> None:
    pool = make_pool(database_url)
    stop = {"flag": False}

    def _sig(*_a):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    log.info("worker up (poll=%ss, handlers=%s)", poll_seconds, sorted(HANDLERS))
    while not stop["flag"]:
        worked = run_once(pool)
        if not worked:
            time.sleep(poll_seconds)
    log.info("worker shutting down")
    pool.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    url = os.environ.get("WORKER_DATABASE_URL") or os.environ["DATABASE_URL"]
    run_forever(url, float(os.environ.get("WORKER_POLL_SECONDS", "5")))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run → pass; whole pg suite; ruff; commit**

Run: `uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg -q && uv run --extra dev ruff check .`

```bash
git add services/agent-spine/src/trax_io_spine/pg/worker.py services/agent-spine/tests/pg/test_worker.py
git commit -m "feat(pg): C2 Task 5 — jobs worker (skip-locked claim, retry x3, dead-letter)"
```

---

### Task 6: apps/web auth shell — supabase client, login, authed API

**Files:**
- Modify: `apps/web/package.json` (`npm install @supabase/supabase-js@^2`)
- Create: `apps/web/src/lib/auth/supabase.ts`
- Create: `apps/web/src/lib/auth/useAuth.tsx`
- Create: `apps/web/src/pages/Login.tsx`
- Modify: `apps/web/src/lib/api/client.ts` (auth header + 401 handling in `request<T>()` ONLY)
- Modify: `apps/web/src/App.tsx` (wrap in `AuthProvider`; unauthenticated + auth-enabled → render `Login`; header gains user email + Sign out)
- Test: `apps/web/src/lib/auth/useAuth.test.tsx`, extend `apps/web/src/App.test.tsx`

**Interfaces:**
- Produces (`supabase.ts`): `export const supabase: SupabaseClient | null` — built from `import.meta.env.VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY`; **null when either is unset → auth-disabled dev mode, exactly today's behavior** (local Docker keeps working with zero env). `export const authEnabled = supabase !== null`.
- Produces (`useAuth.tsx`): `AuthProvider` + `useAuth()` returning `{ session: Session | null, authEnabled: boolean, tenantSlug: string | null, role: string | null, email: string | null, signIn(email, password): Promise<{error: string | null}>, signOut(): Promise<void> }`. `tenantSlug`/`role` come from decoding the access token payload (`JSON.parse(atob(token.split(".")[1]))` → `tenant_id` uuid + `tenant_role`) — BUT routes need the SLUG: resolve it by calling the existing BFF once... NO — keep it simple and synchronous: the BFF's `tenant_uuids` maps slug→uuid; the frontend needs the reverse. Add `VITE_TENANT_SLUGS` (JSON object `{"<uuid>": "<slug>"}`) baked at build for C2's single-tenant deploy (`{"753b64bd-9885-4639-b116-8f2c5c497232": "aeronta-demo"}`); a claims uuid with no mapping → `tenantSlug = null` → Login screen shows "no tenant access". (C4's signup flow replaces this with a `/v1/auth/whoami` endpoint; YAGNI now.)
- Produces (`client.ts` changes): `request<T>()` reads the current access token (module-level `let accessToken: string | null` + `export function setAccessToken(t: string | null)` called by `AuthProvider` on every auth-state change — avoids async session lookups in the hot path) and attaches `Authorization: Bearer ...` when set; a 401 response when `authEnabled` dispatches `window.dispatchEvent(new Event("aeronta:unauthorized"))` (AuthProvider listens → `signOut()`); `DEFAULT_TENANT` export unchanged, but add `export function activeTenant(): string` returning the auth tenantSlug when set else `DEFAULT_TENANT` — sweep the app's `DEFAULT_TENANT` call sites (`grep -rn "DEFAULT_TENANT" apps/web/src`) to use `activeTenant()`.
- Dev-mode invariant: with no `VITE_SUPABASE_*` env, EVERY existing test and the local Docker flow behave byte-identically (no login screen, no header token).

- [ ] **Step 1: Write the failing tests**

`apps/web/src/lib/auth/useAuth.test.tsx` — follow the file-local conventions of `apps/web/src/lib/api/client.test.ts` (vitest, `vi.stubGlobal`/`vi.mock`). Cases: (1) `authEnabled=false` when env unset → `session` null, `signIn` returns error "auth disabled"; (2) with a mocked supabase client (mock `@supabase/supabase-js` `createClient` returning `{auth: {getSession, onAuthStateChange, signInWithPassword, signOut}}` fakes), `signIn` success populates session + `setAccessToken` called + `tenantSlug` decoded from a hand-built JWT payload with `tenant_id` mapped through `VITE_TENANT_SLUGS`; (3) `signOut` clears. Extend `App.test.tsx`: with mocked auth-enabled + no session → Login renders; with session → nav renders (existing assertions).

Write the complete test file (the implementer transcribes the described cases into concrete vitest code following the neighboring files' mock style — `client.test.ts` shows the exact `vi.stubGlobal("fetch", ...)` and env-stub patterns this repo uses; `import.meta.env` stubs via `vi.stubEnv`).

- [ ] **Step 2: Implement**

`supabase.ts`:

```typescript
import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const anon = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;

export const supabase: SupabaseClient | null =
  url && anon ? createClient(url, anon) : null;
export const authEnabled = supabase !== null;

const slugMapRaw = import.meta.env.VITE_TENANT_SLUGS as string | undefined;
export const tenantSlugByUuid: Record<string, string> = slugMapRaw
  ? (JSON.parse(slugMapRaw) as Record<string, string>)
  : {};
```

`useAuth.tsx` (complete provider):

```tsx
import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from "react";
import type { Session } from "@supabase/supabase-js";
import { setAccessToken } from "@/lib/api/client";
import { authEnabled, supabase, tenantSlugByUuid } from "./supabase";

interface AuthState {
  session: Session | null;
  authEnabled: boolean;
  tenantSlug: string | null;
  role: string | null;
  email: string | null;
  signIn: (email: string, password: string) => Promise<{ error: string | null }>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

function claimsOf(session: Session | null): { tenant?: string; role?: string } {
  const token = session?.access_token;
  if (!token) return {};
  try {
    const payload = JSON.parse(atob(token.split(".")[1])) as {
      tenant_id?: string;
      tenant_role?: string;
    };
    return { tenant: payload.tenant_id, role: payload.tenant_role };
  } catch {
    return {};
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);

  useEffect(() => {
    if (!supabase) return;
    void supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const { data: sub } = supabase.auth.onAuthStateChange((_evt, s) => setSession(s));
    const onUnauthorized = () => void supabase.auth.signOut();
    window.addEventListener("aeronta:unauthorized", onUnauthorized);
    return () => {
      sub.subscription.unsubscribe();
      window.removeEventListener("aeronta:unauthorized", onUnauthorized);
    };
  }, []);

  useEffect(() => {
    setAccessToken(session?.access_token ?? null);
  }, [session]);

  const signIn = useCallback(async (email: string, password: string) => {
    if (!supabase) return { error: "auth disabled" };
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    return { error: error?.message ?? null };
  }, []);

  const signOut = useCallback(async () => {
    if (supabase) await supabase.auth.signOut();
  }, []);

  const value = useMemo<AuthState>(() => {
    const { tenant, role } = claimsOf(session);
    return {
      session,
      authEnabled,
      tenantSlug: tenant ? (tenantSlugByUuid[tenant] ?? null) : null,
      role: role ?? null,
      email: session?.user?.email ?? null,
      signIn,
      signOut,
    };
  }, [session, signIn, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
```

`client.ts` — add near the top (and use in `request<T>()`):

```typescript
let accessToken: string | null = null;
export function setAccessToken(token: string | null): void {
  accessToken = token;
}
```

In `request<T>()`: merge `...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {})` into headers; after the `!response.ok` check add — before throwing — `if (response.status === 401 && accessToken) window.dispatchEvent(new Event("aeronta:unauthorized"));`. Add `activeTenant()` (imports `useAuth` is a hook — NOT usable here; instead export a module-level `let activeTenantSlug: string | null` + `export function setActiveTenant(slug: string | null)` called by AuthProvider alongside `setAccessToken`, and `export function activeTenant(): string { return activeTenantSlug ?? DEFAULT_TENANT; }`). Sweep `DEFAULT_TENANT` consumers to `activeTenant()`.

`Login.tsx` — minimal branded card (follow existing page structure/tailwind conventions; no new design system): email + password inputs, submit calls `signIn`, error line on failure, spinner while pending. `App.tsx`: wrap the router in `<AuthProvider>`; inside, a gate component: `authEnabled && !session` → `<Login />`; header (existing flex row from the theme toggle work) gains `{email}` text + "Sign out" button when session present.

- [ ] **Step 3: Run → pass; build; lint; commit**

Run: `cd apps/web && npm install && npm test && npm run build && npm run lint`
Expected: all existing 288+ tests green (dev-mode invariant) + new auth tests; build + lint clean.

```bash
git add apps/web/package.json apps/web/package-lock.json apps/web/src
git commit -m "feat(web): C2 Task 6 — Supabase auth shell (login, session, authed API client)"
```

---

### Task 7: apps/web — Members page + tenant switcher

**Files:**
- Create: `apps/web/src/lib/api/members.ts` (client methods + types)
- Create: `apps/web/src/pages/Members.tsx`
- Create: `apps/web/src/components/TenantSwitcher.tsx`
- Modify: `apps/web/src/App.tsx` (nav entry "Members" — rendered only when `role` is `admin`/`owner`; switcher in header when authenticated)
- Test: `apps/web/src/pages/Members.test.tsx`

**Interfaces:**
- Consumes: Task 4's routes (`GET/POST/PATCH/DELETE /v1/tenants/{t}/members*`, `POST /v1/auth/activate-tenant`), Task 6's `useAuth` (`role`, `tenantSlug`) + authed `request<T>`.
- Produces (`members.ts`): `interface Member { user_id: string; role: string; created_at: string; email?: string }`; `getMembers(tenant): Promise<Member[]>`; `inviteMember(tenant, email, role): Promise<{user_id: string; role: string}>`; `updateMemberRole(tenant, userId, role): Promise<void>`; `removeMember(tenant, userId): Promise<void>`; `activateTenant(tenantUuid): Promise<void>` — all via the existing `request<T>` helper (auth header comes free).
- Produces (`Members.tsx`): table of members (email, role badge, created); invite form (email + role select `admin|planner|viewer`); per-row role select (disabled for self and for `owner` rows unless caller is owner) + Remove button with the existing `useFocusTrap` confirm-dialog pattern (copy the RollbackConfirmDialog structure from Part Drill-Down); 409 from remove/patch surfaces as inline error text "cannot remove the last owner". TanStack Query: `useQuery(["members", tenant])` + mutations invalidating `["members", tenant]`.
- Produces (`TenantSwitcher.tsx`): renders ONLY when authenticated AND `Object.keys(tenantSlugByUuid).length > 1`; select of known tenants; on change → `activateTenant(uuid)` → `supabase.auth.refreshSession()` → `window.location.reload()`. (Single-tenant C2 deploy: invisible — shipping it dark keeps C4 wiring trivial.)
- Nav gating: "Members" appended to `NAV_ITEMS` conditionally by role — follow the existing NAV_ITEMS render; route `/members`.

- [ ] **Step 1: Write the failing tests**

`apps/web/src/pages/Members.test.tsx` — follow the existing page-test conventions (e.g. `Overview.test.tsx`: QueryClient wrapper, fetch stub as URL router). Cases: renders member rows from stubbed GET; invite form POSTs and refreshes the list; remove → confirm dialog → DELETE called; 409 on delete renders the last-owner message; role select PATCHes. Wrap in a stubbed `AuthProvider` context value (`role: "owner"`, `tenantSlug: "aeronta-demo"`) via a test helper that renders with a mocked `useAuth` (use `vi.mock("@/lib/auth/useAuth", ...)`).

- [ ] **Step 2: Implement per the Interfaces block** (complete `members.ts` + `Members.tsx` + `TenantSwitcher.tsx`; App.tsx nav/route/switcher wiring)

- [ ] **Step 3: Run → pass; build; lint; commit**

Run: `cd apps/web && npm test && npm run build && npm run lint`

```bash
git add apps/web/src
git commit -m "feat(web): C2 Task 7 — Members management page + tenant switcher"
```

---

### Task 8: Vercel config — rewrite + build wiring

**Files:**
- Create: `apps/web/vercel.json`
- Modify: `apps/web/README.md` (or create a short `apps/web/DEPLOY.md` if no README exists — check first) documenting the three build-time envs + deploy command
- Test: local `npm run build` with the envs set + `npx vercel build` dry-run (no deploy in this task)

**Interfaces:**
- Produces `apps/web/vercel.json`:

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "rewrites": [
    { "source": "/v1/:path*", "destination": "https://RAILWAY_BFF_URL_PLACEHOLDER/v1/:path*" }
  ]
}
```

The literal string `RAILWAY_BFF_URL_PLACEHOLDER` is committed on purpose — Task 9 produces the real Railway domain, and Task 11 substitutes it (one-line edit) before the first deploy; a grep for the placeholder is Task 11's pre-flight check. (Vercel rewrites cannot read env vars — the domain must be literal.)
- Deploy-time envs documented: `VITE_SUPABASE_URL=https://sluoxufnqwusmtckklnv.supabase.co`, `VITE_SUPABASE_ANON_KEY=<anon key — public, from supabase projects api-keys>`, `VITE_TENANT_SLUGS={"753b64bd-9885-4639-b116-8f2c5c497232":"aeronta-demo"}`, `VITE_BFF_URL=` (EMPTY on Vercel — same-origin through the rewrite; the client's `BASE_URL` fallback logic must treat empty string as same-origin: adjust `client.ts`'s `BASE_URL` line to `(import.meta.env.VITE_BFF_URL as string | undefined) ?? DEFAULT_BFF_URL` → `import.meta.env.VITE_BFF_URL !== undefined ? (import.meta.env.VITE_BFF_URL as string) : DEFAULT_BFF_URL` so `VITE_BFF_URL=""` yields relative `/v1/...` URLs).

- [ ] **Step 1: Write vercel.json + the BASE_URL empty-string fix + docs; verify**

Run: `cd apps/web && VITE_BFF_URL= VITE_SUPABASE_URL=https://sluoxufnqwusmtckklnv.supabase.co VITE_SUPABASE_ANON_KEY=test VITE_TENANT_SLUGS='{}' npm run build && npm test`
Expected: build green; tests green (BASE_URL fix covered by extending `client.test.ts` with an empty-string-env case via `vi.stubEnv("VITE_BFF_URL", "")` asserting a relative URL fetch).

- [ ] **Step 2: Commit**

```bash
git add apps/web/vercel.json apps/web/src/lib/api/client.ts apps/web/src/lib/api/client.test.ts apps/web/README.md apps/web/DEPLOY.md 2>/dev/null; git add apps/web/src
git commit -m "feat(web): C2 Task 8 — vercel.json rewrite + same-origin BFF base URL"
```

---

### Task 9: Live Supabase auth activation — hook grants, hook registration, test user, smoke script

**Files:**
- Create: `supabase/migrations/20260721000007_auth_hook_grants.sql`
- Modify: `services/agent-spine/tests/pg/auth_shim.sql` (add `supabase_auth_admin` role so the migration applies on the harness)
- Create: `deploy/aeronta_smoke.py`
- Modify: `supabase/README.md` (record hook registration + smoke usage)

**Interfaces:**
- Produces migration 0007: everything `supabase_auth_admin` (the role GoTrue runs hooks as) needs to execute the claims hook — the hook is `security invoker`, so the role must read the tables itself:

```sql
-- C2: let GoTrue's supabase_auth_admin run the claims hook (security invoker).
-- On the local harness the role is created by auth_shim.sql; on live Supabase
-- it already exists.
grant usage on schema public to supabase_auth_admin;
grant execute on function public.custom_access_token_hook(jsonb) to supabase_auth_admin;
grant execute on function public.try_uuid(text) to supabase_auth_admin;
grant select on public.memberships, public.tenant_preferences to supabase_auth_admin;
create policy memberships_auth_hook_read on public.memberships
  for select to supabase_auth_admin using (true);
create policy tenant_preferences_auth_hook_read on public.tenant_preferences
  for select to supabase_auth_admin using (true);
```

(The `using (true)` policies are deliberate and narrow: GoTrue must see every membership to mint claims; the role is not reachable from app code.)
- Produces `auth_shim.sql` addition (inside the existing `do $$` block): `if not exists (select from pg_roles where rolname = 'supabase_auth_admin') then create role supabase_auth_admin nologin; end if;`
- Produces `deploy/aeronta_smoke.py` — env-gated live smoke (spec §8). Env: `AERONTA_SMOKE_EMAIL`, `AERONTA_SMOKE_PASSWORD`, `AERONTA_SUPABASE_URL` (default `https://sluoxufnqwusmtckklnv.supabase.co`), `AERONTA_ANON_KEY`, optional `AERONTA_BFF_URL`. Behavior: (1) password-grant sign-in `POST {supabase}/auth/v1/token?grant_type=password` (headers `apikey: <anon>`, json `{email, password}`); (2) decode the access token payload and ASSERT `tenant_id` + `tenant_role` claims exist (the hook fired) — print them; (3) if `AERONTA_BFF_URL` set: GET `{bff}/v1/tenants/aeronta-demo/recommendations` with the token → expect 200 with rows; same URL without token → expect 401; GET `.../members` with the token → expect 200 when role is admin/owner, 403 for planner/viewer; (4) exit non-zero with a named failure on any mismatch; missing env → print `SKIP (env unset)` and exit 0. Plain stdlib + httpx; ~80 lines; runnable as `uv run --extra bff python ../../deploy/aeronta_smoke.py` from services/agent-spine or with any python that has httpx.

- [ ] **Step 1: Migration + shim; harness green; push live**

Run: `cd services/agent-spine && uv run --extra dev --extra pg-test pytest tests/pg -q` (fresh container applies 0007 with the shim's new role) — all green. Then push:

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer" && set -a && source deploy/_local_extract/aeronta-supabase.env && set +a && supabase db push --db-url "postgresql://postgres.sluoxufnqwusmtckklnv:${AERONTA_SUPABASE_DB_PASSWORD}@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
```
Expected: `Applying migration 20260721000007_auth_hook_grants.sql... Finished`.

- [ ] **Step 2: Register the hook + enable email auth on the live project**

Use the Supabase Management API with the CLI's stored access token (config.toml does not exist in this repo and is not introduced — the one-runner rule covers schema only; auth settings are project config):

```bash
TOKEN=$(cat ~/.supabase/access-token)
curl -s -X PATCH "https://api.supabase.com/v1/projects/sluoxufnqwusmtckklnv/config/auth" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"hook_custom_access_token_enabled": true,
       "hook_custom_access_token_uri": "pg-functions://postgres/public/custom_access_token_hook",
       "external_email_enabled": true, "mailer_autoconfirm": true}' | head -c 400
```
Expected: JSON echo containing `"hook_custom_access_token_enabled":true`. (`mailer_autoconfirm: true` so the smoke test user needs no email round-trip in C2; revisit at C4 signup.) If the token file path differs (CLI version), find it via `supabase login --help` / `ls ~/.supabase`; if the API field names differ, `curl GET` the same endpoint first and match the existing field naming — the GET response is the authority.

- [ ] **Step 3: Create the smoke user + membership**

Generate a password into the env file (`AERONTA_SMOKE_EMAIL=smoke@aeronta.test`, `AERONTA_SMOKE_PASSWORD=<openssl rand>` — append, umask 077, never echo). Then, with the service key (from `supabase projects api-keys --project-ref sluoxufnqwusmtckklnv`, `service_role` row — also store as `AERONTA_SERVICE_KEY` in the env file):

```bash
curl -s -X POST "https://sluoxufnqwusmtckklnv.supabase.co/auth/v1/admin/users" \
  -H "apikey: $AERONTA_SERVICE_KEY" -H "Authorization: Bearer $AERONTA_SERVICE_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"$AERONTA_SMOKE_EMAIL\", \"password\": \"$AERONTA_SMOKE_PASSWORD\", \"email_confirm\": true}"
```
Capture `"id"` from the response → insert membership as postgres via psycopg one-liner: `insert into memberships (user_id, tenant_id, role) values ('<id>', '753b64bd-9885-4639-b116-8f2c5c497232', 'owner') on conflict do nothing`.

- [ ] **Step 4: Write + run the smoke script (claims stage)**

Write `deploy/aeronta_smoke.py` per the Interfaces contract. Run WITHOUT `AERONTA_BFF_URL` (BFF not deployed yet):

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer" && set -a && source deploy/_local_extract/aeronta-supabase.env && set +a && AERONTA_ANON_KEY=$(supabase projects api-keys --project-ref sluoxufnqwusmtckklnv -o json | python3 -c "import json,sys; print([k for k in json.load(sys.stdin) if k['name']=='anon'][0]['api_key'])") "services/agent-spine/.venv/bin/python" deploy/aeronta_smoke.py
```
Expected output: `sign-in OK · claims: tenant_id=753b64bd-... tenant_role=owner · BFF checks skipped (no AERONTA_BFF_URL)`. **This proves the hook mints tenant claims on real logins — the load-bearing assertion of the whole auth design.** (Adapt the api-keys JSON parsing to the CLI's actual output shape; the table output from earlier in this project shows `anon` and `service_role` rows exist.)

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20260721000007_auth_hook_grants.sql services/agent-spine/tests/pg/auth_shim.sql deploy/aeronta_smoke.py supabase/README.md
git commit -m "feat(auth): C2 Task 9 — hook grants for supabase_auth_admin, live hook registration, smoke script"
```

---

### Task 10: Railway — project, two services, deploy the BFF + worker  ⚠️ USER GATE

**Files:** none committed except `supabase/README.md`/`deploy/` notes at the end — this is an ops task driven from the CLI.

**Interfaces:**
- Consumes: the built image definition `deploy/bff.Dockerfile` (Task 2 added `--extra pg`), migration state on live (Tasks 1, 9), env-file secrets.
- Produces: Railway project `aeronta` with services `bff` (public domain, the URL Task 11 substitutes into vercel.json) and `worker`; all variables set; both services healthy. Record the BFF public URL in `deploy/_local_extract/aeronta-supabase.env` as `AERONTA_RAILWAY_BFF_URL=` and in `supabase/README.md`'s live-project table (the URL is not secret).

- [ ] **Step 1: USER GATE — install + login**

The controller PAUSES and asks the user to run: `brew install railway && railway login` (interactive browser auth). Verify with `railway whoami`. Do not proceed without it.

- [ ] **Step 2: Create project + services**

From the repo root: `railway init -n aeronta` (creates + links the project; writes nothing tracked — confirm `git status` clean afterward, `.railway/` if created is per-machine: add to `.gitignore` if it appears). Then `railway add -s bff && railway add -s worker`. If the CLI subcommands differ on the installed version (`railway add` flags change between majors), run `railway --help`/`railway add --help` first and use the equivalent — the deliverable is the two named services, not specific flags.

- [ ] **Step 3: Configure service settings + variables**

For BOTH services set builder = Dockerfile with path `deploy/bff.Dockerfile` and root directory `/` (repo root build context — the Dockerfile COPYs `services/*`). Worker overrides the start command to `uv run --no-sync python -m trax_io_spine.pg.worker`. Settings not exposed by the installed CLI version are set in the Railway dashboard (Service → Settings) — record which path was used in the task report. Variables (values from the env file — set via `railway variables -s bff --set "KEY=VAL"` per key, never echoed to the report):
  - `bff`: `DATABASE_URL=postgresql://trax_app.sluoxufnqwusmtckklnv:<AERONTA_TRAX_APP_PASSWORD>@aws-0-us-east-1.pooler.supabase.com:5432/postgres`, `PLANNER_TENANT=aeronta-demo`, `AUTH_JWKS_URL=https://sluoxufnqwusmtckklnv.supabase.co/auth/v1/.well-known/jwks.json`, `SUPABASE_URL=https://sluoxufnqwusmtckklnv.supabase.co`, `SUPABASE_SERVICE_KEY=<AERONTA_SERVICE_KEY>`
  - `worker`: `WORKER_DATABASE_URL=postgresql://trax_seed.sluoxufnqwusmtckklnv:<AERONTA_TRAX_SEED_PASSWORD>@aws-0-us-east-1.pooler.supabase.com:5432/postgres`, `WORKER_POLL_SECONDS=5`
  - Note the trax_app URL for the BFF is CORRECT post-migration-0006 (resolve_tenant_slug is security definer) — this is the retirement of the bypassrls workaround, live.

- [ ] **Step 4: Deploy + verify**

`railway up -s bff` then `railway up -s worker` (from repo root; watch build logs — the image builds the four path-dep packages, expect several minutes first time). Generate the BFF public domain: `railway domain -s bff` → record as `AERONTA_RAILWAY_BFF_URL`. Set the health check path `/healthz` on the bff service. Verify live:

```bash
curl -s "https://<bff-domain>/healthz"                                  # {"ok": true, "tenants": ["aeronta-demo"]}
curl -s -o /dev/null -w "%{http_code}\n" "https://<bff-domain>/v1/tenants/aeronta-demo/recommendations"   # 401 (auth ON)
set -a && source deploy/_local_extract/aeronta-supabase.env && set +a
AERONTA_BFF_URL="https://<bff-domain>" AERONTA_ANON_KEY=... "services/agent-spine/.venv/bin/python" deploy/aeronta_smoke.py   # full pass incl. 200-with-token + members
railway logs -s worker | tail -5    # heartbeat lines, no crash loop
```
Expected: exactly as annotated. Any failure: read `railway logs -s bff`, fix, redeploy — do not proceed to Task 11 with a failing smoke.

- [ ] **Step 5: Record + commit**

Append `AERONTA_RAILWAY_BFF_URL=...` to the env file; update `supabase/README.md` live-table (Railway project/services/URL). Commit:

```bash
git add supabase/README.md
git commit -m "ops: C2 Task 10 — Railway aeronta project live (bff + worker), URL recorded"
```

---

### Task 11: Vercel — deploy apps/web, end-to-end smoke

**Files:**
- Modify: `apps/web/vercel.json` (substitute `RAILWAY_BFF_URL_PLACEHOLDER` → the real Railway BFF domain from Task 10)
- Modify: `supabase/README.md` (live URL table gains the Vercel production URL)

- [ ] **Step 1: Substitute the rewrite target**

`grep -n RAILWAY_BFF_URL_PLACEHOLDER apps/web/vercel.json` → replace with the Task 10 domain (https URL, no trailing slash). A remaining placeholder grep must come back empty.

- [ ] **Step 2: Set Vercel env + deploy**

```bash
cd apps/web
vercel link --yes --project aeronta-inventory     # binds this dir to the existing project
vercel env add VITE_SUPABASE_URL production       # value: https://sluoxufnqwusmtckklnv.supabase.co
vercel env add VITE_SUPABASE_ANON_KEY production  # value: the anon key
vercel env add VITE_TENANT_SLUGS production       # value: {"753b64bd-9885-4639-b116-8f2c5c497232":"aeronta-demo"}
vercel env add VITE_BFF_URL production            # value: (empty string — same-origin)
vercel deploy --prod
```
(`vercel env add` prompts on stdin — pipe values: `printf '%s' "$VAL" | vercel env add NAME production`.) Expected: production deployment URL printed (`https://aeronta-inventory-<hash>.vercel.app` + the stable `aeronta-inventory.vercel.app` alias if free).

- [ ] **Step 3: End-to-end smoke through the rewrite**

```bash
curl -s -o /dev/null -w "%{http_code}\n" "https://<vercel-prod-url>/"                                      # 200 (the app)
curl -s -o /dev/null -w "%{http_code}\n" "https://<vercel-prod-url>/v1/tenants/aeronta-demo/dashboard"     # 401 — rewrite reached the BFF, auth on
AERONTA_BFF_URL="https://<vercel-prod-url>" ... deploy/aeronta_smoke.py                                    # full pass THROUGH the rewrite
```
Then a real browser check (controller may use the in-app browser): open the prod URL → Login renders → sign in with the smoke user → Overview loads with the 4-part demo data → Members page lists the smoke user as owner. Zero console errors.

- [ ] **Step 4: Commit**

```bash
git add apps/web/vercel.json supabase/README.md
git commit -m "ops: C2 Task 11 — apps/web live on Vercel, end-to-end smoke through the rewrite"
```

---

### Task 12: Bookkeeping

**Files:**
- Modify: `ROADMAP.md` — C-track: C2 `[x]` dated with deliverables (Railway bff+worker live, Vercel live, JWT auth on, members mgmt, hook active, migrations 0006-0007) + surviving carry-forwards (59K scale gate still pending a full-network snapshot; custom domain, CI, OAuth providers → C4/later).
- Modify: `TASKS.md` — dated session entry (C2 executed: what shipped, live URLs, test counts, deviations if any accrued during execution).
- Modify: `CLAUDE.md` — Section A: agent-spine test row's extra rename (`pg-test`), new env vars (AUTH_JWKS_URL etc.) one-liner, `deploy/aeronta_smoke.py` mention, live-deploy pointer to supabase/README.md.
- Modify: `docs/superpowers/specs/2026-07-21-c2-cloud-deploy-design.md` — status line → "Shipped <date>"; `docs/superpowers/specs/2026-07-20-commercialization-architecture-design.md` §10 — C2 row marked shipped.

- [ ] **Step 1: Make the edits** (each file's existing format; exact live URLs from the env file's non-secret entries)
- [ ] **Step 2: Commit**

```bash
git add ROADMAP.md TASKS.md CLAUDE.md docs/superpowers/specs
git commit -m "docs: C2 cloud-deploy bookkeeping — ROADMAP, TASKS, CLAUDE.md, spec status"
```
