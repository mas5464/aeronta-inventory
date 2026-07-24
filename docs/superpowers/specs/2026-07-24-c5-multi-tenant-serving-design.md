# C5 — Multi-Tenant Serving + Scheduled Recompute

**Date:** 2026-07-24
**Status:** Approved (design walkthrough complete)
**Owner:** Miguel Sosa
**Parent spec:** [2026-07-20-commercialization-architecture-design.md](2026-07-20-commercialization-architecture-design.md) (§6 compute tier/cadence, §8 app shell)
**Closes:** the C4 final-review carry-forward — *"a paying self-serve signup cannot reach the product; manual tenant activation required"* ([2026-07-23-c4-billing-marketing-design.md](2026-07-23-c4-billing-marketing-design.md), and the "Known limitation — manual tenant activation" section of [deploy/C4_ROLLOUT.md](../../../deploy/C4_ROLLOUT.md), which this sub-project **deletes**).

**Builds on the live stack:** Supabase `aeronta-inventory` (migrations 0001–0012), FastAPI BFF + jobs worker on Railway, `apps/web` on Vercel, C3 self-serve ingest, C4 billing. Nothing here needs new infrastructure — both subsystems reuse what C1–C4 built.

---

## Decisions locked

| Decision | Choice |
|---|---|
| **Scope** | **Serving + scheduled recompute** in one sub-project (two independent subsystems, clean internal boundaries, six task-groups — §8) |
| **Scheduler** | **pg_cron in Supabase** — a SQL cron inserts `jobs` rows the existing worker drains. No new service, no new deploy target. Verified live: **pg_cron 1.6.4 available, not yet installed** |
| **Recompute source** | Replay the tenant's **last successful ingest payload** with a fresh `now` — the Storage batch is never deleted, and `run_ingest` already runs the engine at `datetime.now(UTC)` |
| **Queue on recompute** | **Fresh queue every night** — the recommendation set is replaced, matching upload-ingest behavior (see §3.6 for the recorded consequence + upgrade path) |
| **Preserved on recompute** | `writeback_ledger` + `kill_switches` (non-negotiable, §3.1); `decisions` already survives |
| **Tenant identity** | The **JWT `tenant_id` claim stays authoritative**; the URL slug is addressing only |
| **Unresolvable slug** | **403**, not 404 — no existence oracle for org slugs, and it preserves today's observable behavior |

---

## 1. Architecture

Two independent subsystems under one theme: *any tenant, no manual steps*.

### Subsystem A — dynamic tenant serving

Three places hard-code one tenant today:

| Wall | Today | C5 |
|---|---|---|
| `bff/asgi.py` boot | resolves `PLANNER_TENANT` once → single-entry `stores` / `tenant_uuids` / `members_stores` / `ingest_stores` | a **`TenantRegistry`** resolves slug↔uuid on demand and lazily builds + caches per-tenant stores |
| `bff/auth.py` middleware | `tenant_uuids.get(slug)` → `None` for an unknown slug → **403 tenant mismatch** (the *first* wall a new tenant hits) | resolve via the registry, then apply the **same** claim match |
| `apps/web` | tenant slug from the build-time `VITE_TENANT_SLUGS` uuid→slug map | **`GET /v1/auth/whoami`** returns slug/role/name + the membership list from the verified token |

**The isolation model does not change.** Dynamic resolution widens *which* tenants are reachable, never *who* can reach them: the middleware resolves slug→uuid and still requires `claims.tenant_id == resolved_uuid`; RLS remains the hard boundary beneath that.

Why this is small: `PgPlannerStore.__init__` holds only a pool reference (its own docstring: *"Construction is cheap: neither this class nor PgWritebackTarget cache anything beyond the pool reference"*), and `app.py`'s `_store()` is already a dict lookup. The BFF is *already shaped* for multi-tenancy — it just resolves one tenant at boot instead of on demand.

### Subsystem B — scheduled recompute

```
pg_cron (nightly 03:00 UTC, inside Postgres)
   └─ select public.enqueue_due_recomputes()
        → INSERT jobs(kind='recompute', tenant_id, payload) per ELIGIBLE tenant
        ▼
existing worker loop (unchanged)  →  HANDLERS["recompute"]
        └─ replay that tenant's last successful ingest payload
           → fresh `now` → engine → preserve-mode re-seed
```

### Unchanged by design
The worker's claim loop (already multi-tenant — it processes whatever job it claims, keyed by payload `tenant_id`), the recommendation engine, RLS policies, the C4 Edge Functions, and `apps/site`.

---

## 2. The serving path

### 2.1 Migration 0013 — `public.tenants_for_current_user()`

Listing a user's memberships is impossible under normal RLS: `memberships` is scoped to `current_tenant_id()`, so a user connected as tenant A cannot see their tenant-B row. One `SECURITY DEFINER` function — the same sanctioned-exception pattern as C4's `create_tenant_for_current_user` — keyed strictly on the caller:

```sql
create function public.tenants_for_current_user()
returns table (tenant_uuid uuid, slug text, name text, role text)
language sql stable security definer set search_path = public as $$
  select t.id, t.slug, t.name, m.role
    from public.memberships m
    join public.tenants t on t.id = m.tenant_id
   where m.user_id = (auth.jwt()->>'sub')::uuid
   order by t.name
$$;
revoke execute on function public.tenants_for_current_user() from public;
grant execute on function public.tenants_for_current_user() to authenticated, trax_app;
```

It can only ever return the caller's own memberships. The BFF invokes it through `tenant_conn(pool, active_uuid, sub=caller_sub)` — `pg/db.py`'s `tenant_claims(tenant_id, role, sub)` already carries `sub`.

### 2.2 `bff/tenant_registry.py` (new)

One unit, one job: turn a slug into a working set of per-tenant stores.

- `uuid_for_slug(slug) -> str | None` — via the existing `resolve_tenant_slug` SECURITY DEFINER function (the only option here: without a uuid we cannot open a `tenant_conn`). Memoized.
- `store_for(slug)` / `members_store_for(slug)` / `ingest_store_for(slug)` — lazily construct `PgPlannerStore` / `MembershipStore` / `IngestJobStore`, cached per slug.

Construction is free (pool reference only), so the cache is convenience, not a performance necessity. An unbounded dict is correct at current scale; **eviction is noted as future work and deliberately not built** (YAGNI). `resolve_tenant_slug` returning `None` is cached as a negative result only for the request's duration — never persisted, so a tenant created seconds ago is reachable immediately.

### 2.3 `AuthMiddleware`

Takes a `tenant_uuid_for(slug)` resolver callable; the static-dict path is retained for tests and dev. An **unresolvable slug returns 403** (unchanged from today) — deliberately not 404, which would make the endpoint an existence oracle for org slugs. The claim-match logic, write-method role floor, and the C4 402 subscription gate are all untouched and keep their current ordering.

### 2.4 `GET /v1/auth/whoami`

Unscoped-authed, beside `activate-tenant` (outside `/v1/tenants/{slug}/…`, so the slug-match assertion doesn't apply):

```json
{ "user_id": "…",
  "active":  { "tenant_uuid": "…", "slug": "…", "name": "…", "role": "owner" } | null,
  "tenants": [ { "tenant_uuid": "…", "slug": "…", "name": "…", "role": "…" } ] }
```

`active: null, tenants: []` is the legitimate mid-signup state (account confirmed, org not yet created) — the frontend shows "create an organization" rather than erroring.

### 2.5 `bff/app.py` + `asgi.py` wiring

`_store()` and the members/ingest lookups resolve through the registry. In `DATABASE_URL` mode `PLANNER_TENANT` becomes **optional** (if set, it merely pre-warms the cache); the dev/in-memory boot paths are unchanged and stay single-tenant.

This also fixes a latent bug: `activate_tenant` does `next(iter(stores.values()))` and 503s on an empty dict — with lazy caching that dict *is* empty at boot, so the route must obtain its store from the registry.

`GET /healthz`'s `{"tenants": sorted(stores)}` changes meaning from "the tenant" to "tenants resolved so far" — documented, since it becomes a cache-warmth signal rather than a deployment fact.

### 2.6 `apps/web`

`useAuth` fetches `whoami` after session load and provides `tenantSlug` + the tenant list; `TenantSwitcher` reads that list. **`VITE_TENANT_SLUGS` and `tenantSlugByUuid` are deleted** — the env var comes out of the Vercel config, and the C4 runbook's "Known limitation — manual tenant activation" section is removed.

### 2.7 Empty-tenant serving

A brand-new tenant has zero `part_keys` / `recommendations`. Every read surface must degrade to a clean empty state (dashboard zeros, empty queue, forecast/BVR with nothing valued) rather than crash on an aggregate over no rows. This is treated as a first-class risk with explicit per-surface tests: **it is the first thing a paying customer sees.**

---

## 3. The recompute path

### 3.1 Preserve-mode seed — the only change to the seed path

`pg/seed.py`'s `seed_store` gains a `preserve: set[str] = frozenset()` parameter subtracted from `_SEEDED_TABLES`. Recompute passes `{"writeback_ledger", "kill_switches"}`:

- **`writeback_ledger`** — the append-only provenance ledger that rollback and the SOC 2 audit posture depend on. A background job must never delete it.
- **`kill_switches`** — a safety control. A tenant that engaged the kill switch must never find it silently disengaged by a nightly job.

`decisions` is already absent from `_SEEDED_TABLES` and stays preserved. Neither preserved table is FK'd to `recommendations`, so keeping them while the queue is replaced remains consistent. **Upload-ingest behavior is byte-for-byte unchanged** (it passes no `preserve`).

### 3.2 Migration 0014 — `public.enqueue_due_recomputes() returns integer`

Pure SQL, so eligibility logic is fully testable on the plain-Postgres harness. Inserts one `jobs(kind='recompute')` row per eligible tenant and returns the count. Eligible =

1. **has a prior successful ingest** (`jobs`, `kind='ingest'`, `status='done'`) — there is nothing to replay otherwise;
2. **`subscription_status ∈ {trialing, active, past_due}`** — lapsed/read-only tenants don't burn compute;
3. **no `ingest`/`recompute` already `queued`|`running`** — the dedup that prevents pile-up when a run is slow or the worker is down.

The new row's payload is the tenant's last successful ingest payload **copied verbatim**, plus `"source": "recompute"`. The job row is therefore self-describing and deterministic: reading a queued row tells you exactly what it will re-run.

### 3.3 pg_cron registration is a rollout step, not a migration

`create extension pg_cron` would fail on the throwaway test container (extension unavailable there), breaking the pg suite. Following the repo's existing precedent that real-Supabase-only steps live in the runbook, the C5 rollout section adds:

```sql
create extension if not exists pg_cron;
select cron.schedule('aeronta-nightly-recompute', '0 3 * * *',
                     $$select public.enqueue_due_recomputes()$$);
```

Verified against the live project: **pg_cron 1.6.4 available, `installed_version = None`.**

### 3.4 Worker handler

`HANDLERS["recompute"]` is a thin wrapper over the C3 ingest handler: same `run_ingest`, preserve-mode on, result tagged `source="recompute"`. The per-tenant `pg_advisory_xact_lock` inside `run_ingest` already serializes a recompute against a concurrent upload — no double-seed.

Validation still runs **before** the lock and the seed, which yields a useful property: if a tenant downgraded plans and their dataset now exceeds the new `key_quota`, the recompute **fails cleanly and the previous data stays intact**.

### 3.5 Visibility

Recompute jobs share the `jobs` table, so C3's ingest-history surface lists them. They are labeled **"Scheduled recompute"** vs **"Upload"** so a tenant isn't confused by runs they didn't start.

### 3.6 Recorded consequence — the queue resets nightly

Because the recommendation set is replaced (locked decision), a planner's triage does not survive the night: a rejected recommendation reappears the next morning as pending. The `decisions` table still records what they did.

This is a deliberate simplicity trade, not an oversight. **Upgrade path if it becomes a complaint:** reconcile on the natural key `(pn, location, rec_type)` — carrying terminal state forward and letting only new/changed keys arrive pending. Reconciliation is required because `recommendation_id` is a fresh `ULID()` per engine run (`recommenders/base.py`), so ids cannot be matched across runs.

---

## 4. Security

- The **JWT claim remains authoritative** for tenant identity; slugs are addressing. Dynamic resolution changes reachability, never authorization.
- Both new SQL functions are `SECURITY DEFINER` but strictly caller-scoped: `tenants_for_current_user()` keys on `auth.jwt()->>'sub'`; `enqueue_due_recomputes()` takes no caller input at all (it is invoked by cron, not by a request path) and only ever writes `jobs` rows. Both get `revoke execute … from public` before their grants.
- `enqueue_due_recomputes()` is **not exposed through any HTTP route** — no tenant can trigger other tenants' compute.
- No new service-role usage: the worker (already sanctioned) executes recomputes; the BFF stays `trax_app`.
- 403-not-404 on unresolvable slugs avoids an org-slug existence oracle.
- RLS is unchanged. Registry-cached stores are per-slug and each still opens `tenant_conn` per query, so a cached store cannot read another tenant's rows.

## 5. Error handling

- Unresolvable slug → 403 (unchanged). Resolver/DB failure during resolution → **503**, matching the C4 write-gate's fail-closed posture rather than a raw 500.
- `whoami` with no memberships → `200` with `active: null, tenants: []` (a valid state, not an error).
- Recompute job failure → the existing worker retry/dead-letter path (retry ×3 → `failed`); validation failures fail the job **without** touching seeded data.
- `enqueue_due_recomputes()` is idempotent under the dedup clause — a double cron firing enqueues nothing extra.

## 6. Testing

- **Postgres/RLS:** `tenants_for_current_user()` returns only the caller's memberships (two users × two tenants, cross-user leakage denied); `enqueue_due_recomputes()` eligibility matrix (no prior ingest → skipped; lapsed subscription → skipped; already queued/running → skipped; eligible → exactly one row with the copied payload) and idempotency on a second call.
- **Registry:** unknown-then-known slug resolution; cached store reuse; `None` for a genuinely absent tenant.
- **Middleware:** dynamically-resolved tenant passes; wrong-claim tenant still 403; unresolvable slug 403; resolver raising → 503; the C4 402 gate and role floor still ordered correctly.
- **`whoami`:** shape, active/list correctness, empty-membership state.
- **Empty tenant:** every read surface (queue, dashboard, part context, forecast, BVR, feeds) returns a valid empty response — the "first thing a paying customer sees" gate.
- **Preserve-mode seed:** a recompute leaves `writeback_ledger` + `kill_switches` rows intact while replacing `recommendations`/`part_keys`/`part_contexts`; an upload-ingest still full-replaces (regression).
- **Worker:** `recompute` handler replays the last payload and tags `source`; concurrent upload+recompute serialize via the advisory lock.
- **`apps/web`:** `useAuth` consumes `whoami`; `TenantSwitcher` renders the returned list; no reference to `VITE_TENANT_SLUGS` remains.
- **Two-tenant e2e (route-mocked):** two different tenants served by one BFF instance without cross-contamination.

## 7. Rollout

1. `supabase db push` migrations 0013–0014.
2. Redeploy BFF (`railway up -s bff`) and worker (`-s worker`) — the worker needs the new handler.
3. Redeploy `apps/web` (prebuilt, per the C4 lesson) and **remove `VITE_TENANT_SLUGS`** from the Vercel project.
4. `create extension pg_cron` + `cron.schedule(...)` (§3.3), then verify with `select * from cron.job`.
5. Verify the first nightly run: `select id, kind, status from jobs where kind='recompute' order by id desc limit 5`.
6. Live gate: sign up a brand-new tenant end-to-end (signup → checkout → upload → recommendations visible) **with no manual activation step** — the acceptance criterion for this whole sub-project.

## 8. Task-group decomposition

Ships as one C5; the plan sequences into six independently-testable groups:

1. **Migrations** — 0013 `tenants_for_current_user()`, 0014 `enqueue_due_recomputes()` + RLS/eligibility tests.
2. **Tenant registry** — `bff/tenant_registry.py` + tests.
3. **BFF integration** — middleware resolver, `whoami`, `app.py`/`asgi.py` wiring, `activate_tenant` fix, empty-tenant hardening.
4. **Frontend** — `useAuth`/`TenantSwitcher` on `whoami`, delete the build-time map.
5. **Recompute** — preserve-mode `seed_store`, worker `recompute` handler, history labeling.
6. **Rollout + bookkeeping** — runbook (incl. pg_cron), remove C4's manual-activation limitation, ROADMAP/TASKS/CLAUDE.md.

Groups 1→2→3 are the serving spine (sequential); 4 depends on 3; 5 depends on 1; 6 last.

## 9. Out of scope / deferred

- **Queue reconciliation across recomputes** (§3.6) — deliberate; upgrade path documented.
- **Registry cache eviction / TTL** — unbounded is correct at current tenant counts; revisit at scale.
- **Per-tenant recompute cadence or opt-out** — one global nightly schedule in v1; a per-tenant override is a `tenant_preferences` change later.
- **Recompute cost controls** (concurrency caps, per-tenant compute budgets) — the dedup clause plus one worker replica bounds this naturally today.
- **Custom domains / per-tenant subdomains** — the app stays at one origin; tenancy is in the token, not the hostname.
- **Multi-region / read replicas**, and **SAML SSO** (a C6+ enterprise concern).

## 10. Risks

| Risk | Mitigation |
|---|---|
| Dynamic resolution weakens isolation | The claim match is unchanged and RLS is untouched; registry stores still open `tenant_conn` per query. Explicit cross-tenant tests at middleware and store layers. |
| pg_cron unavailable or not permitted on the project | Verified available (1.6.4). If installation is blocked, fall back to a worker-internal tick calling the same `enqueue_due_recomputes()` — the SQL function is the seam, so the fallback costs only the trigger. |
| Nightly recompute stampede across many tenants | Eligibility narrows to active-subscription tenants with data; the queued/running dedup prevents pile-up; the worker drains serially. Revisit with a concurrency cap when tenant count grows. |
| A recompute wipes something it shouldn't | Preserve-mode is explicit and directly tested; upload-ingest regression tests prove its behavior is unchanged. |
| Empty new tenant crashes a read surface | Per-surface empty-state tests are a required gate (§6). |
| Planners lose triage nightly (§3.6) | Recorded as a known limitation with a documented upgrade path; `decisions` retains the audit trail. |
