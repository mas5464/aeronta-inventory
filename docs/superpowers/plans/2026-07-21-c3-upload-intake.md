# C3 — Upload Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tenant self-serve uploads CSV/Excel of their inventory data on the live Aeronta stack, and an async ingest job validates it, runs the recommendation engine, and replaces their dataset — producing recommendations end-to-end.

**Architecture:** Approach A — canonical CSVs map to the engine's existing extract JSON shape. Browser uploads direct to Supabase Storage via BFF-minted signed URLs; a `jobs` row triggers the Railway worker, which validates, maps to a temp extract dir, and drives the unchanged `build_stores_from_extract` → `seed_store` replace path. Plus two ledgered hardening items (principal attribution, DB owner rules) and the worker claim-durability fix.

**Tech Stack:** Python ≥3.12 (openpyxl for xlsx, stdlib csv, httpx for Storage REST), psycopg3, Supabase Storage, FastAPI (BFF), React 18/Vite (apps/web), the pg testcontainer harness.

**Spec:** [docs/superpowers/specs/2026-07-21-c3-upload-intake-design.md](../specs/2026-07-21-c3-upload-intake-design.md)

## Global Constraints

- Live facts (reuse verbatim): Supabase ref `sluoxufnqwusmtckklnv`, pooler host `aws-0-us-east-1.pooler.supabase.com:5432` (user `<role>.<ref>`; direct host IPv6-only). Demo tenant `aeronta-demo` uuid `753b64bd-9885-4639-b116-8f2c5c497232`. Secrets ONLY in gitignored `deploy/_local_extract/aeronta-supabase.env` (`AERONTA_*` + `SUPABASE_ACCESS_TOKEN` PAT) — never in repo/image/reports (redact when printing).
- Live schema owned EXCLUSIVELY by `supabase db push --db-url <pooler>` (one-runner rule). The Python `apply_migrations` runner is test-harness only. Migrations are named `2026072100000N_<slug>.sql`, continuing after `20260721000007`.
- Engine input contract (do NOT change the engine): `build_stores_from_extract(extract_dir, *, tenant_id, essentiality_map=None, pool_by_part=False)` reads a dir of `<domain>.json` files, each a JSON list of row dicts with **lowercased eMRO-native keys**. Required domains: `stock_amount`, `stock_level_upload`, `part_master`. The mapper produces these keys (mapping table in Task 3).
- Worker: `trax_io_spine.pg.worker` has an empty `HANDLERS: dict[str, Callable[[dict], None]]`. The claimed job row returns `(id, tenant_id, kind, payload, attempts)`; `payload` is a dict. C3 registers `HANDLERS["ingest"]`.
- New deps behind extras: `openpyxl>=3.1` goes in `services/recommendation-engine`'s `dev`+runtime extra AND agent-spine's `bff` extra path (the worker imports the ingest module). `httpx` already present in agent-spine. Confirm the actual import chain at task time.
- Backend suites (from `services/agent-spine`): `uv run --extra dev --extra bff --extra bvr --extra pg-test pytest -q` stays green (current: pg 99/1, whole 365+/2). Reco suite: `cd services/recommendation-engine && uv run --extra dev pytest -q`. `ruff check` clean (only allowed pre-existing: 2× B905 in agent-spine `tests/bff/test_csv_export.py`). Frontend: `cd apps/web && npm test && npm run build && npm run lint` (current 324 Vitest).
- Test-isolation convention: pg-harness fixtures seed UNIQUE Postgres slugs per file (`acme-c3t<N>`), never plain `acme`.
- CRITICAL GIT SCOPE: every subagent works in the WORKTREE `/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf`; all edits + git ops there; a live `supabase db push`/curl `cd` is command-scoped only. Verify `git status` shows branch `claude/nervous-swirles-424ddf` before committing. Quote paths (repo path has a space).
- Never touch `oracle19c`/MySQL containers. Commit after each task with the task's exact message; each commit leaves suites green.
- User gate: Supabase Storage bucket creation + Storage RLS policies need the Management API / dashboard (Task 1) — the controller runs these with the stored PAT, or pauses for the user. Live smoke (Task 7) is env-gated.

---

### Task 0a: Principal attribution — thread verified caller identity into decisions + writeback

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/pg/store.py` (`PgPlannerStore.__init__`, `_decision`, `approve`; `PgWritebackTarget` construction)
- Modify: `services/agent-spine/src/trax_io_spine/pg/writeback.py` (`write()` principal)
- Modify: `services/agent-spine/src/trax_io_spine/bff/app.py` (`_store` factory reads `request.state.claims`)
- Test: `services/agent-spine/tests/pg/test_pg_store_principal.py`, extend `tests/bff/test_app_pg.py`

**Interfaces:**
- Consumes: C2 middleware stashing verified claims at `request.state.claims` (dict with `sub`, `tenant_role`).
- Produces: `PgPlannerStore(pool, *, tenant_slug, tenant_uuid, open_orders=None, principal="planner")` — `principal` flows to every `_decision(principal=…)` and to `PgWritebackTarget(pool, *, tenant_uuid, open_orders=None, rollback_window_days=90, principal="agent-spine")` whose `write()` records `changed_by_principal=principal`. `bff/app.py`'s `_store(tenant_id)` becomes `_store(tenant_id, request)` and passes `principal=request.state.claims.get("sub")` when claims present (else default). Rollback already uses `req.principal` (the `RollbackRequest` body) — unchanged.

- [ ] **Step 1: Write the failing store test**

`services/agent-spine/tests/pg/test_pg_store_principal.py`:

```python
"""Principal attribution: an explicit principal lands in decisions.principal and
the writeback ledger's changed_by_principal (SOC 2 attributable audit)."""
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
SUB = "11111111-2222-3333-4444-555555555555"


@pytest.fixture()
def store(admin_pool, pg_pool):
    mem = PlannerStore.from_extract(
        tenant_id="acme", extract_dir=str(EXTRACT), now=datetime(2026, 4, 1, tzinfo=UTC)
    )
    report = seed_store(admin_pool, store=mem, slug="acme-c3t0a", name="A")
    return PgPlannerStore(
        pg_pool, tenant_slug="acme-c3t0a", tenant_uuid=report.tenant_uuid, principal=SUB
    ), report.tenant_uuid


def test_decision_records_principal(store, admin_pool):
    pg, tenant_uuid = store
    rid = next(r.recommendation_id for r in pg.queue() if r.approvable)
    result = pg.approve(rid)
    with admin_pool.connection() as conn:
        row = conn.execute(
            "select principal from decisions where tenant_id = %s::uuid and rec_id = %s",
            (tenant_uuid, rid),
        ).fetchone()
    assert row[0] == SUB
    # and the writeback ledger entry
    hist = pg.history(pn=result.writeback.pn, location=result.writeback.location)
    assert hist[-1].changed_by_principal == SUB
```

- [ ] **Step 2: Run → fail** (`principal` kwarg unknown / defaults to "planner")

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_pg_store_principal.py -v`
Expected: FAIL (assert `"planner" == SUB` / `"agent-spine" == SUB`).

- [ ] **Step 3: Implement**

`pg/store.py` `__init__`: add `principal: str = "planner"`, store `self._principal = principal`, and construct the writeback target with it:

```python
    def __init__(self, pool, *, tenant_slug, tenant_uuid, open_orders=None, principal="planner"):
        self._pool = pool
        self.tenant_id = tenant_slug
        self._uuid = tenant_uuid
        self._principal = principal
        self.writeback = PgWritebackTarget(
            pool, tenant_uuid=tenant_uuid, open_orders=open_orders, principal=principal
        )
```

Change `_decision`'s default call sites to pass `principal=self._principal` (edit the method signature default is fine, but pass explicitly from approve/reject/defer/bulk/kill_switch/rollback `_decision(...)` calls — or set `_decision`'s default to read `self._principal`: simplest is `def _decision(self, conn, *, rec_id, action, payload=None, principal=None)` then `principal = principal or self._principal`). 

`pg/writeback.py`: `__init__` gains `principal: str = "agent-spine"` → `self._principal`; in `write()` replace the literal `principal="agent-spine"` in the `_record(...)` call with `principal=self._principal`.

`bff/app.py`: change `_store` to take `request: Request` and read claims:

```python
    from fastapi import Request

    def _store(tenant_id: str, request: Request) -> PlannerStore:
        base = stores.get(tenant_id)
        if base is None:
            raise HTTPException(status_code=404, detail="unknown tenant")
        claims = getattr(request.state, "claims", None)
        if claims and isinstance(base, PgPlannerStore):
            return PgPlannerStore(
                base._pool, tenant_slug=base.tenant_id, tenant_uuid=base._uuid,
                principal=claims.get("sub", "planner"),
            )
        return base
```

Every route calling `_store(tenant_id)` gains a `request: Request` param and passes it: `_store(tenant_id, request)`. (Read-only routes may keep the base store — but threading `request` uniformly is simpler and harmless; do it for the decision routes at minimum: approve/reject/defer/bulk/rollback/killswitch.) Note: the in-memory `PlannerStore` path is unchanged (no principal), so local/dev/tests without claims behave identically.

- [ ] **Step 4: App-level test** — extend `tests/bff/test_app_pg.py`: an authed approve (HS verifier, token `sub`) results in a `decisions.principal` equal to that sub (query via admin_pool). Follow that file's existing verifier+token setup.

- [ ] **Step 5: Run → pass; whole suite; ruff; commit**

Run: `uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg tests/bff -q && uv run --extra dev ruff check .`

```bash
git add services/agent-spine/src/trax_io_spine/pg/store.py services/agent-spine/src/trax_io_spine/pg/writeback.py services/agent-spine/src/trax_io_spine/bff/app.py services/agent-spine/tests/pg/test_pg_store_principal.py services/agent-spine/tests/bff/test_app_pg.py
git commit -m "feat(pg): C3 Task 0a — principal attribution threaded into decisions + writeback ledger"
```

---

### Task 0b: Migration 0008 — DB-layer owner-membership rules

**Files:**
- Create: `supabase/migrations/20260721000008_owner_membership_rules.sql`
- Test: `services/agent-spine/tests/pg/test_c3_owner_rules.py`

**Interfaces:**
- Produces: owner-aware `memberships` UPDATE/DELETE policies REPLACING the C2 (0006) `memberships_update`/`memberships_delete` — an `admin` claim may still manage `planner`/`viewer` rows, but modifying or deleting an `owner` row, or setting any row's role TO `owner`, requires an `owner` claim. INSERT policy (0006) is unchanged except: inserting an `owner` role requires `owner` claim.

- [ ] **Step 1: Write the migration**

`supabase/migrations/20260721000008_owner_membership_rules.sql`:

```sql
-- C3 Task 0b: move owner-specific membership rules into RLS (defense in depth behind
-- the app-layer _require_owner / last-owner guard). Only an owner may create/modify/
-- delete an owner-role membership; admins keep managing planner/viewer rows.
create or replace function public.current_tenant_role() returns text
language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
    ->> 'tenant_role'
$$;

drop policy if exists memberships_insert on public.memberships;
drop policy if exists memberships_update on public.memberships;
drop policy if exists memberships_delete on public.memberships;

create policy memberships_insert on public.memberships for insert to trax_app
  with check (
    tenant_id = (select public.current_tenant_id())
    and (select public.current_tenant_role()) in ('admin', 'owner')
    -- creating an owner requires owner
    and (role <> 'owner' or (select public.current_tenant_role()) = 'owner')
  );

create policy memberships_update on public.memberships for update to trax_app
  using (
    tenant_id = (select public.current_tenant_id())
    and (select public.current_tenant_role()) in ('admin', 'owner')
    -- modifying an existing owner row requires owner
    and (role <> 'owner' or (select public.current_tenant_role()) = 'owner')
  )
  with check (
    tenant_id = (select public.current_tenant_id())
    -- setting a row TO owner requires owner
    and (role <> 'owner' or (select public.current_tenant_role()) = 'owner')
  );

create policy memberships_delete on public.memberships for delete to trax_app
  using (
    tenant_id = (select public.current_tenant_id())
    and (select public.current_tenant_role()) in ('admin', 'owner')
    and (role <> 'owner' or (select public.current_tenant_role()) = 'owner')
  );
```

- [ ] **Step 2: Write the failing tests**

`services/agent-spine/tests/pg/test_c3_owner_rules.py`:

```python
"""Owner-specific membership RLS: admin manages planner/viewer, only owner touches owner."""
import psycopg
import pytest
from tests.pg.conftest import as_tenant

T = "cccccccc-3333-3333-3333-cccccccc0c3b"
OWNER = "00000000-0000-0000-0000-0000000c3b01"
PLANNER = "00000000-0000-0000-0000-0000000c3b02"


@pytest.fixture(scope="module", autouse=True)
def seed(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, 'acme-c3t0b', 'A') "
            "on conflict (id) do nothing",
            (T,),
        )
        conn.execute(
            "insert into memberships (user_id, tenant_id, role) values "
            "(%s, %s, 'owner'), (%s, %s, 'planner') on conflict do nothing",
            (OWNER, T, PLANNER, T),
        )
        conn.commit()


def test_admin_can_manage_planner_row(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, T, role="admin")
        conn.execute(
            "update memberships set role = 'viewer' where user_id = %s and tenant_id = %s",
            (PLANNER, T),
        )
        conn.commit()
    with pg_pool.connection() as conn:  # restore
        as_tenant(conn, T, role="admin")
        conn.execute(
            "update memberships set role = 'planner' where user_id = %s and tenant_id = %s",
            (PLANNER, T),
        )
        conn.commit()


def test_admin_cannot_modify_owner_row(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, T, role="admin")
        # RLS USING excludes the owner row from an admin's UPDATE => 0 rows affected
        cur = conn.execute(
            "update memberships set role = 'planner' where user_id = %s and tenant_id = %s",
            (OWNER, T),
        )
        assert cur.rowcount == 0
        conn.commit()


def test_admin_cannot_promote_to_owner(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, T, role="admin")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "update memberships set role = 'owner' where user_id = %s and tenant_id = %s",
                (PLANNER, T),
            )


def test_owner_can_promote_and_demote(pg_pool):
    with pg_pool.connection() as conn:
        as_tenant(conn, T, role="owner")
        conn.execute(
            "update memberships set role = 'owner' where user_id = %s and tenant_id = %s",
            (PLANNER, T),
        )
        conn.execute(
            "update memberships set role = 'planner' where user_id = %s and tenant_id = %s",
            (PLANNER, T),
        )
        conn.commit()
```

- [ ] **Step 3: Run → pass locally; push live; ruff; commit**

Run: `uv run --extra dev --extra pg-test pytest tests/pg/test_c3_owner_rules.py -v && uv run --extra dev ruff check .`
Then push live (from the worktree root):

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer/.claude/worktrees/nervous-swirles-424ddf" && set -a && source /Users/miguelsosa/Projects/Inventory\ Opmimizer/deploy/_local_extract/aeronta-supabase.env && set +a && supabase db push --db-url "postgresql://postgres.sluoxufnqwusmtckklnv:${AERONTA_SUPABASE_DB_PASSWORD}@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
```
Expected: `Applying migration 20260721000008_owner_membership_rules.sql... Finished`.

```bash
git add supabase/migrations/20260721000008_owner_membership_rules.sql services/agent-spine/tests/pg/test_c3_owner_rules.py
git commit -m "feat(pg): C3 Task 0b — owner-specific membership RLS (migration 0008)"
```

---

### Task 1: Migration 0009 — jobs.result column + Storage bucket & RLS

**Files:**
- Create: `supabase/migrations/20260721000009_jobs_result_uploads.sql`
- Modify: `services/agent-spine/tests/pg/auth_shim.sql` (only if the migration references a Supabase-only role/schema absent on the harness — jobs.result is plain SQL, so likely no shim change; storage RLS lives in Supabase Storage config, NOT this migration)
- Create: `supabase/README.md` addition (bucket + storage policy record)
- Test: `services/agent-spine/tests/pg/test_c3_jobs_result.py`

**Interfaces:**
- Produces: `jobs.result jsonb` (nullable) — the ingest success summary; `jobs.error` (existing) stays the failure/validation payload.
- Produces (Storage, applied live via Management API/dashboard — NOT a SQL migration): a private bucket `tenant-uploads`; a Storage RLS policy so an authenticated user may only `insert`/`select` objects whose path starts with `<their tenant_id>/` (derived from the JWT `tenant_id` claim); the service role bypasses for the worker read.

- [ ] **Step 1: Migration + test**

`supabase/migrations/20260721000009_jobs_result_uploads.sql`:

```sql
-- C3 Task 1: ingest success summary distinct from the error payload.
alter table public.jobs add column result jsonb;
```

`services/agent-spine/tests/pg/test_c3_jobs_result.py`:

```python
"""jobs.result column exists and round-trips JSON."""
import json


def test_jobs_result_roundtrip(admin_pool):
    T = "dddddddd-9999-9999-9999-dddddddd0c31"
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name) values (%s, 'acme-c3t1', 'A') "
            "on conflict (id) do nothing",
            (T,),
        )
        jid = conn.execute(
            "insert into jobs (tenant_id, kind, payload, result) "
            "values (%s, 'ingest', '{}', %s) returning id",
            (T, json.dumps({"keys": 42, "recommendations": 6})),
        ).fetchone()[0]
        conn.commit()
        got = conn.execute("select result from jobs where id = %s", (jid,)).fetchone()[0]
        assert got == {"keys": 42, "recommendations": 6}
```

- [ ] **Step 2: Run local → pass; push migration live**

Run: `uv run --extra dev --extra pg-test pytest tests/pg/test_c3_jobs_result.py -v`
Push live with the same `supabase db push --db-url` command as Task 0b.

- [ ] **Step 3: Create the Storage bucket + RLS (controller/user step)**

Using the stored PAT (`SUPABASE_ACCESS_TOKEN` in the env file) via the Management API, or the dashboard: create a private bucket `tenant-uploads`; add a Storage policy on `storage.objects` for the `authenticated` role restricting `bucket_id = 'tenant-uploads' AND (storage.foldername(name))[1] = (auth.jwt() ->> 'tenant_id')` for both `insert` and `select`. Record the exact policy SQL + bucket settings in `supabase/README.md` (a new "C3 uploads storage" subsection). This is applied to the LIVE project only (Storage config isn't in the migration stream); the harness tests fake Storage.

Note: this step needs live credentials. If run by a subagent without the PAT, report BLOCKED with the exact bucket name + policy SQL for the controller to apply.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/20260721000009_jobs_result_uploads.sql services/agent-spine/tests/pg/test_c3_jobs_result.py supabase/README.md
git commit -m "feat(pg): C3 Task 1 — jobs.result column + tenant-uploads Storage bucket/RLS"
```

---

### Task 2: Canonical model + validation module

**Files:**
- Create: `services/recommendation-engine/src/trax_io_reco/ingest/__init__.py`
- Create: `services/recommendation-engine/src/trax_io_reco/ingest/canonical.py` (the 6-file contract)
- Create: `services/recommendation-engine/src/trax_io_reco/ingest/validate.py`
- Test: `services/recommendation-engine/tests/ingest/test_validate.py`

**Interfaces:**
- Produces (`canonical.py`): `CANONICAL_FILES: dict[str, CanonicalFile]` keyed by canonical name (`parts`, `stock`, `demand_history`, `locations`, `open_orders`, `vendors`); `CanonicalFile = dataclass(name: str, required: bool, required_columns: tuple[str,...], optional_columns: tuple[str,...])`. `REQUIRED_FILES = ("parts", "stock")`. Values per spec §4.
- Produces (`validate.py`): `@dataclass(frozen=True) class IngestError: file: str; row: int | None; column: str | None; message: str`; `def validate(parsed: dict[str, list[dict]], *, key_quota: int | None = None, essentiality_map: dict[str, int] | None = None) -> list[IngestError]` — `parsed` maps canonical name → list of row dicts (lowercased canonical headers, raw string values, from Task 3's parsers). Returns [] when clean. Rules: (1) required files present + required columns present per provided file; (2) per-row: numeric columns (`on_hand`, `quantity`, `unit_cost`, `unit_price`, `lead_time_days`, etc.) parse via a numeric check, `period`/`expected_date` parse via `_parse_date` (import from `trax_io_reco.data.extract_loader`), `part_number`/`location_code` non-empty; (3) referential: every `(part_number, location_code)` in stock/demand_history/open_orders references a `parts` row (and a `locations` row when that file is present); `criticality` values map via `essentiality_map or _DEFAULT_ESSENTIALITY_MAP` (import both) — unknown → error; (4) quota: distinct `(part_number, location_code)` across stock over `key_quota` (when given) → one error `IngestError("stock", None, None, "<n> keys exceeds your plan limit of <key_quota> …")`.
- The numeric column set + which columns are dates live as module constants in `validate.py` (`_NUMERIC_COLUMNS`, `_DATE_COLUMNS` keyed by canonical file).

- [ ] **Step 1: Write the failing tests**

`services/recommendation-engine/tests/ingest/test_validate.py`:

```python
"""Validation rules over parsed canonical rows."""
from trax_io_reco.ingest.validate import IngestError, validate


def _clean() -> dict[str, list[dict]]:
    return {
        "parts": [{"part_number": "P1", "criticality": "AOG", "unit_cost": "100"}],
        "stock": [{"part_number": "P1", "location_code": "MIA", "on_hand": "5"}],
        "demand_history": [
            {"part_number": "P1", "location_code": "MIA", "period": "2026-01-01",
             "quantity": "3"}
        ],
    }


def test_clean_passes():
    assert validate(_clean()) == []


def test_missing_required_file():
    errs = validate({"parts": [{"part_number": "P1"}]})  # no stock
    assert any(e.file == "stock" and "required" in e.message.lower() for e in errs)


def test_missing_required_column():
    p = _clean()
    p["stock"] = [{"part_number": "P1", "location_code": "MIA"}]  # no on_hand
    errs = validate(p)
    assert any(e.file == "stock" and e.column == "on_hand" for e in errs)


def test_non_numeric_quantity():
    p = _clean()
    p["stock"][0]["on_hand"] = "lots"
    errs = validate(p)
    assert any(e.file == "stock" and e.row == 0 and e.column == "on_hand" for e in errs)


def test_bad_date():
    p = _clean()
    p["demand_history"][0]["period"] = "not-a-date"
    errs = validate(p)
    assert any(e.file == "demand_history" and e.column == "period" for e in errs)


def test_referential_unknown_part():
    p = _clean()
    p["stock"].append({"part_number": "GHOST", "location_code": "MIA", "on_hand": "1"})
    errs = validate(p)
    assert any("GHOST" in e.message for e in errs)


def test_unknown_criticality_flagged():
    p = _clean()
    p["parts"][0]["criticality"] = "WHATISTHIS"
    errs = validate(p)
    assert any(e.file == "parts" and e.column == "criticality" for e in errs)


def test_quota_exceeded():
    p = _clean()
    p["stock"] = [
        {"part_number": f"P{i}", "location_code": "MIA", "on_hand": "1"} for i in range(5)
    ]
    p["parts"] = [{"part_number": f"P{i}"} for i in range(5)]
    errs = validate(p, key_quota=3)
    assert any(e.file == "stock" and "exceeds" in e.message for e in errs)
```

- [ ] **Step 2: Run → fail** (`ModuleNotFoundError: trax_io_reco.ingest`). Add `tests/ingest/__init__.py` if the reco suite needs it (check the reco tests' package layout first).

- [ ] **Step 3: Implement `canonical.py` + `validate.py`** per the Interfaces block. `_parse_date`, `_DEFAULT_ESSENTIALITY_MAP` import from `trax_io_reco.data.extract_loader`. Keep numeric-check tolerant of empty optional cells (only flag non-empty non-numeric). Row indices are 0-based over the file's row list.

- [ ] **Step 4: Run → pass; ruff; commit**

Run: `cd services/recommendation-engine && uv run --extra dev pytest tests/ingest -v && uv run --extra dev ruff check .`

```bash
git add services/recommendation-engine/src/trax_io_reco/ingest services/recommendation-engine/tests/ingest
git commit -m "feat(reco): C3 Task 2 — canonical model v1 contract + validation module"
```

---

### Task 3: Parsers (csv/xlsx) + mapper to the engine extract shape

**Files:**
- Create: `services/recommendation-engine/src/trax_io_reco/ingest/parse.py`
- Create: `services/recommendation-engine/src/trax_io_reco/ingest/mapper.py`
- Modify: `services/recommendation-engine/pyproject.toml` (add `openpyxl>=3.1` to the runtime deps or a new `ingest` extra pulled by dev)
- Test: `services/recommendation-engine/tests/ingest/test_parse.py`, `services/recommendation-engine/tests/ingest/test_mapper.py`

**Interfaces:**
- Produces (`parse.py`): `def parse_csv(name: str, data: bytes) -> list[dict]` — stdlib `csv.DictReader`, headers lowercased+stripped, all values kept as stripped strings. `def parse_xlsx(data: bytes) -> dict[str, list[dict]]` — openpyxl, one sheet per canonical name (sheet titles lowercased); returns only sheets whose name is in `CANONICAL_FILES`. `def parse_uploads(files: dict[str, bytes], *, xlsx: bytes | None = None) -> dict[str, list[dict]]` — merges per-file CSVs and/or a workbook into the `{canonical_name: rows}` dict the validator consumes.
- Produces (`mapper.py`): `def to_extract_dir(parsed: dict[str, list[dict]], out_dir: Path, *, tenant_id: str) -> None` — writes the engine domain JSON files into `out_dir` (each a JSON list of dicts with lowercased eMRO keys) + a `manifest.json` (`{tenant_id, extract_date: <today ISO>}`). Mapping table (canonical col → eMRO key):
  - `parts` → `part_master.json`: part_number→`hostpartid`, description→`partdescription`, criticality→`hostpartcriticalid`, part_class→`hostparttypeid`, unit_cost→`marketunitcost` AND `averagecost`, repairable→`partrepairable`, shelf_life_days→`shelflife`, hazmat→`hazmat`, ata_chapter→`atachapter`, is_kit→`ispartkit`.
  - `stock` → `stock_amount.json`: part_number→`hostpartid`, location_code→`hostlocid`, on_hand→`onhandnew`, allocated→`allocated`, in_repair→`inrepair` — AND `stock_level_upload.json`: same keys + current_rop→`rop`, current_eoq→`eoq`, current_safety_stock→`safetylevel`, current_max→`stockmax`.
  - `demand_history` → split by the part's `part_class` (rotable → `demand_history_rotables.json`, else → `demand_history_expendables.json`; absent class → expendables): part_number→`hostpartid`, location_code→`hostlocid`, quantity→`historyamount`, period→`historybegdate`, transaction_type→`transactiontype`.
  - `locations` → `location_master.json`: location_code→`hostlocid`, parent_location_code→`hostparentlocid`.
  - `open_orders` → `order_plan.json`: part_number→`hostpartid`, location_code→`hostlocid`, quantity→`planquantity`, expected_date→`planrcvdate`, order_type→`ordertypeid`.
  - `vendors` → `pn_vendor_price.json`: part_number→`hostpartid`, vendor_code→`hostvendorlocid`, unit_price→`price`, lead_time_days→`processinglength`, min_order_qty→`minoq`, condition→`condition`, preferred→`preferred`.
  - The required domains `stock_amount`, `stock_level_upload`, `part_master` are always written (empty list if the canonical file was absent — but `parts`/`stock` are required so they're present). Only write optional domain files when their canonical source is present.

- [ ] **Step 1: Write the failing parser + mapper tests**

`tests/ingest/test_parse.py`:

```python
from trax_io_reco.ingest.parse import parse_csv, parse_uploads, parse_xlsx


def test_parse_csv_lowercases_headers():
    data = b"Part_Number,On_Hand\nP1,5\n"
    rows = parse_csv("stock", data)
    assert rows == [{"part_number": "P1", "on_hand": "5"}]


def test_parse_uploads_merges_files():
    files = {"parts": b"part_number\nP1\n", "stock": b"part_number,location_code,on_hand\nP1,MIA,5\n"}
    parsed = parse_uploads(files)
    assert set(parsed) == {"parts", "stock"}
    assert parsed["stock"][0]["location_code"] == "MIA"


def test_parse_xlsx_sheets(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "parts"
    ws.append(["part_number"])
    ws.append(["P1"])
    ws2 = wb.create_sheet("stock")
    ws2.append(["part_number", "location_code", "on_hand"])
    ws2.append(["P1", "MIA", "5"])
    p = tmp_path / "u.xlsx"
    wb.save(p)
    parsed = parse_xlsx(p.read_bytes())
    assert parsed["parts"] == [{"part_number": "P1"}]
    assert parsed["stock"][0]["on_hand"] == "5"
```

`tests/ingest/test_mapper.py`:

```python
import json
from pathlib import Path

from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.ingest.mapper import to_extract_dir


def test_mapper_produces_loadable_extract(tmp_path):
    parsed = {
        "parts": [{"part_number": "P1", "part_class": "rotable", "unit_cost": "100",
                   "criticality": "AOG"}],
        "stock": [{"part_number": "P1", "location_code": "MIA", "on_hand": "5",
                   "current_rop": "3", "current_eoq": "10", "current_safety_stock": "2",
                   "current_max": "20"}],
        "demand_history": [{"part_number": "P1", "location_code": "MIA",
                            "period": "2026-01-01", "quantity": "3"}],
    }
    out = tmp_path / "extract"
    out.mkdir()
    to_extract_dir(parsed, out, tenant_id="t1")
    # required domain files exist with the mapped eMRO keys
    pm = json.loads((out / "part_master.json").read_text())
    assert pm[0]["hostpartid"] == "P1" and pm[0]["marketunitcost"] == "100"
    sa = json.loads((out / "stock_amount.json").read_text())
    assert sa[0]["onhandnew"] == "5" and sa[0]["hostlocid"] == "MIA"
    slu = json.loads((out / "stock_level_upload.json").read_text())
    assert slu[0]["rop"] == "3"
    # rotable demand routed to the rotables file
    assert (out / "demand_history_rotables.json").exists()
    # and the whole thing loads through the real engine loader
    fs, inv, tid, keys = build_stores_from_extract(str(out), tenant_id="t1")
    assert ("P1", "MIA") in keys
```

- [ ] **Step 2: Run → fail; implement `parse.py` + `mapper.py`; add openpyxl dep**

Run: `cd services/recommendation-engine && uv run --extra dev pytest tests/ingest/test_parse.py tests/ingest/test_mapper.py -v` (fails: no module). Add `openpyxl>=3.1` to pyproject, `uv sync --extra dev`, implement, re-run to green.

- [ ] **Step 3: ruff; commit**

```bash
git add services/recommendation-engine/src/trax_io_reco/ingest/parse.py services/recommendation-engine/src/trax_io_reco/ingest/mapper.py services/recommendation-engine/pyproject.toml services/recommendation-engine/uv.lock services/recommendation-engine/tests/ingest
git commit -m "feat(reco): C3 Task 3 — csv/xlsx parsers + canonical→extract mapper"
```

---

### Task 4: Ingest handler + worker claim-durability fix

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/pg/ingest.py` (the handler + a Storage-reader protocol)
- Modify: `services/agent-spine/src/trax_io_spine/pg/worker.py` (claim-commit-before-run + stale-`running` reclaim; register the ingest handler)
- Test: `services/agent-spine/tests/pg/test_c3_ingest_handler.py`, extend `tests/pg/test_worker.py`

**Interfaces:**
- Consumes: Task 2/3's `parse_uploads`/`validate`/`to_extract_dir` (import from `trax_io_reco.ingest`), `PlannerStore.from_extract`, `seed_store` (Task 0a store), `jobs.result` (Task 1).
- Produces (`ingest.py`):
  - `class StorageReader(Protocol): def download(self, path: str) -> bytes: ...`
  - `class HttpxStorageReader: def __init__(self, supabase_url: str, service_key: str, bucket: str = "tenant-uploads")` — `download(path)` GETs `{url}/storage/v1/object/{bucket}/{path}` with the service key; non-2xx → `IngestStorageError`.
  - `def run_ingest(conn, pool, payload: dict, *, storage: StorageReader, tenant_name: str = "") -> dict` — downloads each `payload["files"]`, `parse_uploads` (routing `.xlsx` paths to `parse_xlsx`), `validate(..., key_quota=<tenant's quota>)`; on errors returns `{"status": "failed", "errors": [asdict…]}` (the caller writes `jobs.error`); on clean → `to_extract_dir` into a `tempfile.TemporaryDirectory`, `PlannerStore.from_extract(tenant_id=payload["tenant_slug"], extract_dir=tmp)`, `seed_store(pool, store=…, slug=payload["tenant_slug"], name=tenant_name)`, returns `{"status": "done", "result": {"files": [...], "keys": <n>, "recommendations": <n>, "seeded_at": <iso>}}`. Reads the tenant's `key_quota` from `tenants` via `conn`.
- Produces (`worker.py`): `HANDLERS["ingest"]` wired to a thin adapter that builds the `StorageReader` from `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` env and calls `run_ingest`, then writes `jobs.result`/`jobs.error`/`status` accordingly. **Durability:** `run_once` commits the claim (`status='running'`) in its own transaction BEFORE invoking the handler; the handler runs; the terminal update is a second transaction. The claim query also reclaims `running` jobs whose `claimed_at` is older than `STALE_SECONDS` (default 300) while `attempts < MAX_ATTEMPTS`.

- [ ] **Step 1: Write the failing handler test** (fake StorageReader, real pg + real engine)

`services/agent-spine/tests/pg/test_c3_ingest_handler.py`:

```python
"""Ingest handler end-to-end: sample canonical CSVs → validate → engine → seed."""
import pytest
from tests.pg.conftest import as_tenant  # noqa: F401

from trax_io_spine.pg.ingest import run_ingest

T = "eeeeeeee-4444-4444-4444-eeeeeeee0c34"
PARTS = b"part_number,part_class,unit_cost,criticality\nP1,rotable,100,AOG\n"
STOCK = (b"part_number,location_code,on_hand,current_rop,current_eoq,"
         b"current_safety_stock,current_max\nP1,MIA,5,3,10,2,20\n")
DEMAND = b"part_number,location_code,period,quantity\nP1,MIA,2026-01-01,3\n"


class FakeStorage:
    def __init__(self, blobs):
        self._blobs = blobs

    def download(self, path):
        return self._blobs[path]


@pytest.fixture()
def tenant(admin_pool):
    with admin_pool.connection() as conn:
        conn.execute(
            "insert into tenants (id, slug, name, key_quota) values (%s, 'acme-c3t4', 'A', 5000) "
            "on conflict (id) do nothing",
            (T,),
        )
        conn.commit()
    return T


def _payload():
    return {
        "tenant_id": T, "tenant_slug": "acme-c3t4", "batch_id": "b1",
        "files": {"parts": "acme-c3t4/b1/parts.csv", "stock": "acme-c3t4/b1/stock.csv",
                  "demand_history": "acme-c3t4/b1/demand.csv"},
        "uploaded_by": "u1",
    }


def test_clean_ingest_seeds(tenant, admin_pool, pg_pool):
    storage = FakeStorage({
        "acme-c3t4/b1/parts.csv": PARTS,
        "acme-c3t4/b1/stock.csv": STOCK,
        "acme-c3t4/b1/demand.csv": DEMAND,
    })
    with admin_pool.connection() as conn:
        out = run_ingest(conn, pg_pool, _payload(), storage=storage, tenant_name="A")
    assert out["status"] == "done"
    assert out["result"]["keys"] >= 1
    # recommendations landed for the tenant
    with admin_pool.connection() as conn:
        n = conn.execute(
            "select count(*) from recommendations where tenant_id = %s::uuid", (T,)
        ).fetchone()[0]
    assert n == out["result"]["recommendations"]


def test_dirty_ingest_fails_without_seeding(tenant, admin_pool, pg_pool):
    bad_stock = b"part_number,location_code,on_hand\nP1,MIA,lots\n"
    storage = FakeStorage({
        "acme-c3t4/b1/parts.csv": PARTS,
        "acme-c3t4/b1/stock.csv": bad_stock,
        "acme-c3t4/b1/demand.csv": DEMAND,
    })
    with admin_pool.connection() as conn:
        # ensure no prior rows
        conn.execute("delete from recommendations where tenant_id = %s::uuid", (T,))
        conn.commit()
        out = run_ingest(conn, pg_pool, _payload(), storage=storage, tenant_name="A")
    assert out["status"] == "failed" and out["errors"]
    with admin_pool.connection() as conn:
        n = conn.execute(
            "select count(*) from recommendations where tenant_id = %s::uuid", (T,)
        ).fetchone()[0]
    assert n == 0
```

- [ ] **Step 2: Worker durability test** — extend `tests/pg/test_worker.py`: enqueue a job, monkeypatch a handler that asserts the row is ALREADY `status='running'` when it runs (proving the claim committed first), and a handler that raises leaves the row reclaimable (not lost). Follow the file's `_enqueue`/`_status` helpers.

- [ ] **Step 3: Run → fail; implement `ingest.py` + the worker changes; register handler**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_c3_ingest_handler.py tests/pg/test_worker.py -v` (fails: no module). Implement, re-run. Keep the C2 worker retry×3/dead-letter semantics intact (the existing worker tests must stay green).

- [ ] **Step 4: Whole pg suite + reco suite + ruff; commit**

Run: `uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg -q && cd ../recommendation-engine && uv run --extra dev pytest -q && cd ../agent-spine && uv run --extra dev ruff check .`

```bash
git add services/agent-spine/src/trax_io_spine/pg/ingest.py services/agent-spine/src/trax_io_spine/pg/worker.py services/agent-spine/tests/pg/test_c3_ingest_handler.py services/agent-spine/tests/pg/test_worker.py
git commit -m "feat(pg): C3 Task 4 — ingest handler + worker claim-durability + stale reclaim"
```

---

### Task 5: BFF upload/ingest/poll routes

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/bff/ingest_routes.py`
- Create: `services/agent-spine/src/trax_io_spine/pg/uploads.py` (signed-URL minter + a `SignedUrlMinter` protocol)
- Modify: `services/agent-spine/src/trax_io_spine/bff/app.py` (mount the router; store an `upload_minter` on app.state; wire from asgi)
- Modify: `services/agent-spine/src/trax_io_spine/bff/asgi.py` (build the minter from env)
- Test: `services/agent-spine/tests/pg/test_c3_ingest_routes.py`

**Interfaces:**
- Consumes: Task 1's `jobs`/`jobs.result`, Task 2's `CANONICAL_FILES`, the C2 middleware role floor (writes require `planner`+ — the middleware already 403s `viewer` writes; these routes verify claims presence too).
- Produces (`pg/uploads.py`):
  - `class SignedUrlMinter(Protocol): def mint(self, path: str) -> str: ...` (returns a signed PUT URL)
  - `class HttpxSignedUrlMinter: def __init__(self, supabase_url, service_key, bucket="tenant-uploads")` — `mint(path)` POSTs `{url}/storage/v1/object/upload/sign/{bucket}/{path}` with the service key, returns the signed URL (`{url}/storage/v1{token_path}`); non-2xx → `UploadMintError`. (Verify the exact Storage signed-upload REST shape at build time against the live project; the protocol seam lets tests fake it.)
- Produces (`ingest_routes.py`): `router = APIRouter()`:
  - `POST /v1/tenants/{tenant_id}/uploads` — body `{files: [canonical_name, …]}` (subset of `CANONICAL_FILES`; unknown name → 422); generates a `batch_id` (uuid4 — but the worker/tests must be deterministic: accept an injected id in tests via a module seam, or just uuid4 and assert shape); returns `{batch_id, targets: {name: {url, path}}}` where `path = f"{tenant_uuid}/{batch_id}/{name}"`; 401 if no claims; role floor handled by middleware.
  - `POST /v1/tenants/{tenant_id}/ingest` — body `{batch_id, files: {name: path}}`; inserts a `jobs` row (`kind='ingest'`, payload per §3 incl. `tenant_id=<uuid>`, `tenant_slug`, `uploaded_by=claims['sub']`); returns `{job_id}`. `parts`+`stock` must be present in `files` or 422.
  - `GET /v1/tenants/{tenant_id}/ingest/{job_id}` — `{status, result, errors}` (errors = parsed `jobs.error` when failed).
  - `GET /v1/tenants/{tenant_id}/ingest` — recent ingest jobs (id, status, created_at, uploaded_by, result summary).
  - Handlers read `app.state.upload_minter`; absent → 503 on the uploads route (mint) but ingest/poll still work.
- `app.py`: `create_planner_app(..., upload_minter=None)`; `app.state.upload_minter = upload_minter`; `app.include_router(ingest_router)`. `asgi.py` DATABASE_URL branch builds `HttpxSignedUrlMinter` from `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` when present.

- [ ] **Step 1: Write the failing route tests** (fake minter, real pg)

`services/agent-spine/tests/pg/test_c3_ingest_routes.py`: use the C2 pattern from `tests/pg/test_members.py` — HS verifier, planner/viewer tokens, a `FakeMinter` (`mint(path) -> f"https://signed/{path}"`), seed a tenant `acme-c3t5`. Cases: (1) `POST /uploads` as planner with `{files:["parts","stock"]}` → 200, targets contain both with `path` = `{tenant_uuid}/{batch_id}/parts` etc.; (2) unknown file name → 422; (3) `POST /uploads` as viewer → 403 (middleware floor); (4) `POST /ingest` without `stock` → 422; (5) `POST /ingest` valid → `{job_id}`, and a `jobs` row exists with kind=ingest + payload `uploaded_by` = token sub; (6) `GET /ingest/{id}` → status `queued`; (7) `GET /ingest` lists it. Build the app with `create_planner_app({}, verifier=HsVerifier(...), tenant_uuids={...}, upload_minter=FakeMinter())` plus a `jobs`-capable pool — note the routes need a pool to insert jobs; pass it via app.state or a members-store-like seam (mirror how members_stores gets the pool). Confirm the exact wiring against `app.py`/`test_members.py`.

- [ ] **Step 2: Run → fail; implement `uploads.py` + `ingest_routes.py` + app/asgi wiring**

- [ ] **Step 3: Run → pass; whole suite; ruff; commit**

Run: `uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg tests/bff -q && uv run --extra dev ruff check .`

```bash
git add services/agent-spine/src/trax_io_spine/bff/ingest_routes.py services/agent-spine/src/trax_io_spine/pg/uploads.py services/agent-spine/src/trax_io_spine/bff/app.py services/agent-spine/src/trax_io_spine/bff/asgi.py services/agent-spine/tests/pg/test_c3_ingest_routes.py
git commit -m "feat(bff): C3 Task 5 — upload/ingest/poll routes + signed-URL minter"
```

---

### Task 6: Frontend — upload panel + ingest history

**Files:**
- Create: `apps/web/src/lib/api/ingest.ts` (types + client methods)
- Create: `apps/web/src/features/feeds/UploadPanel.tsx`
- Create: `apps/web/src/features/feeds/IngestHistory.tsx`
- Modify: `apps/web/src/features/feeds/DataConnections.tsx` (mount the panel + history)
- Test: `apps/web/src/features/feeds/UploadPanel.test.tsx`

**Interfaces:**
- Consumes: Task 5 routes; C2 `useAuth` (`role`, `tenantSlug`), the authed `request<T>`.
- Produces (`ingest.ts`): types `CanonicalFileName`, `UploadTargets`, `IngestJob` (`{job_id, status, result?, errors?}`); `mintUploadUrls(tenant, files): Promise<{batch_id, targets}>`; `putToStorage(url, file): Promise<void>` (a plain `fetch(url, {method:"PUT", body:file})` — signed URL, no auth header needed); `createIngest(tenant, batch_id, files): Promise<{job_id}>`; `getIngest(tenant, job_id): Promise<IngestJob>`; `listIngests(tenant): Promise<IngestJob[]>`.
- Produces (`UploadPanel.tsx`): the 6 canonical dropzones (accept `.csv,.xlsx`), required/optional labels, per-file "download template" (a small client-generated CSV of the header row from a `CANONICAL_COLUMNS` const mirrored from the spec), an upload→"Run ingest"→poll flow (TanStack Query polling `getIngest` on an interval until terminal), rendering a success summary (keys/recs + a link to `/workbench`) or a grouped-by-file error table (reuse the existing table primitives). Role-gated: controls render only for `role` ∈ `planner|admin|owner`; `viewer` sees history only.
- Produces (`IngestHistory.tsx`): a table from `listIngests` (when, uploaded_by, status badge, key count).

- [ ] **Step 1: Write the failing test** (follow `Members.test.tsx` conventions — mocked `useAuth`, fetch stub as URL router, a stubbed File)

`UploadPanel.test.tsx` cases: renders dropzones for a planner; hidden for a viewer (history only); selecting a file + Run ingest calls mint→PUT→createIngest then polls to a `done` summary; a `failed` poll renders the grouped error table with the row/column/message. Stub `getIngest` to return `queued` then `done`/`failed` across polls.

- [ ] **Step 2: Implement per the Interfaces block**

- [ ] **Step 3: Run → pass; build; lint; commit**

Run: `cd apps/web && npm test && npm run build && npm run lint`

```bash
git add apps/web/src
git commit -m "feat(web): C3 Task 6 — upload panel + ingest history in Data & Connections"
```

---

### Task 7: Live smoke — ingest stage + real end-to-end

**Files:**
- Modify: `deploy/aeronta_smoke.py` (optional ingest stage)
- Create: `deploy/sample_upload/` (parts.csv, stock.csv, demand_history.csv — a tiny valid canonical batch)

**Interfaces:**
- Produces: when `AERONTA_SMOKE_INGEST=1` and `AERONTA_BFF_URL` set, the smoke script (after the existing sign-in) mints upload URLs, PUTs the three sample CSVs, creates an ingest job, polls to `done`, and asserts `result.keys >= 1` + the queue endpoint now returns rows for `aeronta-demo`. Env unset → the stage is skipped (the existing SKIP semantics preserved).

- [ ] **Step 1: Add the sample batch + the ingest stage** (stdlib + httpx, mirroring the existing stages; named failures + exit codes).

- [ ] **Step 2: Controller runs it live** (after Task 5/6 deployed — but the handler/routes are testable pre-deploy; the LIVE run happens in Task 8's deploy or here if Railway/Vercel are redeployed). Redeploy `bff`+`worker` to Railway and `apps/web` to Vercel with the new code (same `railway up`/`vercel deploy --prod` as C2 Task 10/11), then:

```bash
cd "/Users/miguelsosa/Projects/Inventory Opmimizer" && set -a && source deploy/_local_extract/aeronta-supabase.env && set +a && ANON=$(supabase projects api-keys --project-ref sluoxufnqwusmtckklnv --output-format json | python3 -c "import json,sys; a=[k for k in json.load(sys.stdin) if k.get('name')=='anon'][0]; print(a.get('api_key') or a.get('key',''))") && AERONTA_ANON_KEY="$ANON" AERONTA_BFF_URL="https://aeronta-inventory.vercel.app" AERONTA_SMOKE_INGEST=1 .claude/worktrees/nervous-swirles-424ddf/services/agent-spine/.venv/bin/python .claude/worktrees/nervous-swirles-424ddf/deploy/aeronta_smoke.py
```
Expected: the ingest stage prints `ingest OK · job done · keys=N recs=M`. Note: this REPLACES aeronta-demo's seeded data with the sample batch — acceptable for the demo tenant (re-seed via `trax-io-pg-seed` if the richer demo data is wanted back; record which).

- [ ] **Step 3: Commit**

```bash
git add deploy/aeronta_smoke.py deploy/sample_upload
git commit -m "ops: C3 Task 7 — live ingest smoke stage + sample canonical batch"
```

---

### Task 8: Bookkeeping

**Files:**
- Modify: `ROADMAP.md` (C3 `[x]` dated + deliverables + the closed carry-forwards: principal attribution ✓, DB owner rules ✓, worker durability ✓, tenant-id-in-payload ✓; remaining: 59K scale gate, delta uploads, connectors), C4 (billing) next.
- Modify: `TASKS.md` (dated C3 session entry).
- Modify: `CLAUDE.md` Section A (the `trax_io_reco.ingest` package + `openpyxl` dep; new BFF ingest routes + `tenant-uploads` bucket; reco test note).
- Modify: `docs/superpowers/specs/2026-07-21-c3-upload-intake-design.md` status → shipped; parent spec §10 C3 row shipped.

- [ ] **Step 1: Make the edits** (each file's existing format; live URLs unchanged).
- [ ] **Step 2: Commit**

```bash
git add ROADMAP.md TASKS.md CLAUDE.md docs/superpowers/specs
git commit -m "docs: C3 upload-intake bookkeeping — ROADMAP, TASKS, CLAUDE.md, spec status"
```
