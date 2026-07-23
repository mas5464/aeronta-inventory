# C4 — Billing + Marketing Site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add self-serve Stripe billing (webhook-mirrored, tier-banded, 14-day trial), a self-serve signup funnel, plan-driven quota + a lapsed→read-only write-gate, an in-app billing page, and an Astro marketing/docs site — turning Aeronta Inventory into a revenue-collecting SaaS.

**Architecture:** Supabase **Edge Functions (Deno)** own all Stripe I/O (webhook mirror + checkout/portal) and are the sole writers to the billing mirror + `tenants` billing columns. The FastAPI BFF gains only a 402 write-gate and a read-only `/billing` endpoint. `apps/web` (Vite/React) gains signup + billing surfaces; a new **Astro** app (`apps/site`) is the public marketing/docs/pricing site. Prices live in Stripe, mirrored into Supabase, rendered from the mirror — no dollar amount in code.

**Tech Stack:** Supabase Postgres + Edge Functions (Deno/TypeScript, `stripe` + `@supabase/supabase-js`), FastAPI/psycopg (existing BFF), React 18 + Vite + Tailwind + shadcn + TanStack Query (existing `apps/web`), Astro + React islands (new `apps/site`), Stripe Billing (Checkout + Customer Portal + webhooks), Playwright.

**Spec:** [docs/superpowers/specs/2026-07-23-c4-billing-marketing-design.md](../specs/2026-07-23-c4-billing-marketing-design.md)

## Global Constraints

- **Price-agnostic:** no dollar amount in code. Prices live in Stripe → mirrored `prices`/`products` → pricing page renders from the mirror. Finalizing prices = editing Stripe (+ a pricing-page rebuild), never a code change.
- **Billing runtime = Supabase Edge Functions (Deno).** The `supabase-community/nextjs-subscription-payments` repo is a schema/logic *reference*, not the runtime. No Next.js.
- **Edge Functions are the ONLY Stripe I/O and the ONLY writers to the billing mirror + `tenants` billing columns.** The `stripe-webhook` function is the second sanctioned service-role writer (alongside the C3 worker); it writes *only* billing tables + `tenants` billing columns.
- **RLS on every new table.** `products`/`prices`/`plan_tiers` public-read (active); `subscriptions` tenant-scoped read; `leads` anon insert-only; all mirror + `tenants` billing writes service-role only. **No member write path to `tenants` plan/quota.**
- **Trial:** 14-day, **card required** (`payment_method_collection='always'`, `trial_period_days=14`).
- **Lapse → read-only, never lockout.** Reads are NEVER gated. Writes gated to `subscription_status ∈ {trialing, active, past_due}` → HTTP **402** otherwise. `past_due` stays writable (degrade through dunning); `canceled`/`unpaid`/`paused` → read-only; `none`/`incomplete`/`incomplete_expired` → provisioning (writes blocked, "finish subscribing").
- **Billing = owner role.** `create-checkout-session` and `create-portal-link` are owner-gated (403 otherwise).
- **Email confirmation stays required** (`mailer_autoconfirm` off).
- **Tiers:** `plan_tiers` seeded `starter=5000`, `growth=25000`, `scale=100000`; `enterprise` is contact-us only (not self-serve). `tenants.plan_tier` CHECK already allows `trial|starter|growth|scale|enterprise` (C1).
- **Migrations** are plain-SQL in `supabase/migrations/`, timestamp-named, RLS-in-same-migration; applied by the pg test harness (`services/agent-spine/tests/pg/conftest.py`) against a throwaway Postgres 16 and by `supabase db push` live. Existing max is `20260721000009`.
- **Test commands:** pg suite `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg` (Docker/testcontainers); `apps/web` `cd apps/web && npm test` (Vitest); edge functions `cd supabase/functions && deno test --allow-env --allow-net`; `apps/site` `cd apps/site && npm run build`.
- **Deploy hygiene (from the C3-era incident):** `apps/site` deploys to its **own** Vercel project via CLI from `apps/site`, or Git-integration with Root Directory = `apps/site` — never root-built.

---

## File Structure

**New — migrations (`supabase/migrations/`):**
- `20260723000010_billing_tenants.sql` — `subscription_status` enum, `tenants` billing columns, `plan_tiers` (seeded) + RLS.
- `20260723000011_billing_stripe_mirror.sql` — `products`, `prices`, `subscriptions`, `stripe_events` + RLS.
- `20260723000012_billing_leads_and_org_rpc.sql` — `leads` + RLS, `create_tenant_for_current_user` RPC.

**New — Edge Functions (`supabase/functions/`):**
- `_shared/stripe.ts` (Stripe client factory), `_shared/supabase.ts` (service-role client), `_shared/cors.ts` (CORS headers).
- `create-checkout-session/index.ts`, `create-portal-link/index.ts`, `stripe-webhook/index.ts` (+ `stripe-webhook/sync.ts` for the mirror/tenant sync logic, unit-testable without HTTP).
- `*/deno.test.ts` per function; `supabase/config.toml` (functions `verify_jwt` config).

**Modified — BFF (`services/agent-spine/src/trax_io_spine/bff/`):**
- `auth.py` — `AuthMiddleware` gains an optional `subscription_status_for` callable + the 402 write-gate.
- `app.py` — `create_planner_app` gains `subscription_status_for` param (threaded to the middleware) + the `GET .../billing` route.
- `asgi.py` — in `DATABASE_URL` mode, wire `subscription_status_for` to a pool-backed reader.
- `billing.py` (NEW) — `billing_summary(conn, tenant_uuid) -> BillingSummary` (status + usage read helper).

**Modified/New — apps/web (`apps/web/src/`):**
- `lib/api/billing.ts` (NEW) — `getBilling`, `createCheckoutSession`, `createPortalLink`.
- `lib/api/useSubscription.ts` (NEW) — `useSubscription()` hook.
- `features/billing/BillingPage.tsx` (NEW) + `SignupWizard.tsx` (NEW) + `SubscriptionBanner.tsx` (NEW).
- `App.tsx` — routes `/billing`, `/signup`; nav "Billing" (owner-gated); mount `<SubscriptionBanner/>`.
- `features/feeds/UploadPanel.tsx` — over-quota error gains an "Upgrade" CTA.

**New — Astro site (`apps/site/`):**
- `package.json`, `astro.config.mjs`, `tailwind.config.mjs` (consumes the shared preset), `tsconfig.json`.
- `src/layouts/Base.astro`, `src/pages/{index,product,pricing,security,contact}.astro`, `src/pages/docs.mdx`, `src/lib/supabase.ts`, `src/components/ContactForm.tsx` (React island), `public/robots.txt`, `astro`-generated `sitemap`.

**New — shared design tokens (`packages/tailwind-preset/`):** `index.js` — the Airvoyant Tailwind theme extracted from `apps/web`, consumed by both `apps/web` and `apps/site`.

**Modified — rollout:**
- `deploy/aeronta_smoke.py` — env-gated billing stage.
- `deploy/C4_ROLLOUT.md` (NEW) — Stripe products/prices, secrets, webhook registration, function + site deploy runbook.
- `ROADMAP.md`, `TASKS.md`, `CLAUDE.md` — bookkeeping.

---

## GROUP 1 — Data model

### Task 1: Migration 0010 — `tenants` billing columns, `subscription_status` enum, `plan_tiers`

**Files:**
- Create: `supabase/migrations/20260723000010_billing_tenants.sql`
- Test: `services/agent-spine/tests/pg/test_c4_billing_schema.py`

**Interfaces:**
- Produces: enum `public.subscription_status`; columns `tenants.stripe_customer_id`, `.stripe_subscription_id`, `.subscription_status`, `.current_period_end`, `.trial_ends_at`; table `public.plan_tiers(tier text pk, key_quota int, display_name text, sort int)` seeded starter/growth/scale; RLS `plan_tiers` public-read.

- [ ] **Step 1: Write the failing test**

```python
# services/agent-spine/tests/pg/test_c4_billing_schema.py
# The conftest applies ALL migrations against a throwaway Postgres; these assert
# the 0010 objects exist with the right shape and RLS.
import psycopg
import pytest

def _superuser(pg_url: str):
    return psycopg.connect(pg_url)  # conftest's admin URL fixture — see below

def test_subscription_status_enum_values(pg_admin_conn):
    rows = pg_admin_conn.execute(
        "select enumlabel from pg_enum e join pg_type t on t.oid=e.enumtypid "
        "where t.typname='subscription_status' order by enumsortorder"
    ).fetchall()
    assert [r[0] for r in rows] == [
        "trialing","active","past_due","canceled","incomplete",
        "incomplete_expired","unpaid","paused",
    ]

def test_tenants_billing_columns(pg_admin_conn):
    cols = {r[0] for r in pg_admin_conn.execute(
        "select column_name from information_schema.columns "
        "where table_name='tenants' and table_schema='public'").fetchall()}
    assert {"stripe_customer_id","stripe_subscription_id","subscription_status",
            "current_period_end","trial_ends_at"} <= cols

def test_plan_tiers_seeded(pg_admin_conn):
    rows = dict(pg_admin_conn.execute(
        "select tier, key_quota from plan_tiers order by sort").fetchall())
    assert rows == {"starter":5000,"growth":25000,"scale":100000}

def test_plan_tiers_public_read_rls(pg_admin_conn):
    # anon can read active tiers (public pricing page); anon cannot write.
    pg_admin_conn.execute("set role anon")
    got = pg_admin_conn.execute("select count(*) from plan_tiers").fetchone()[0]
    assert got == 3
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg_admin_conn.execute("insert into plan_tiers (tier,key_quota) values ('x',1)")
    pg_admin_conn.execute("reset role")
```

If a `pg_admin_conn` fixture (a superuser connection to the migrated throwaway DB) does not already exist in `tests/pg/conftest.py`, add it there in this step, mirroring the existing pool fixtures (a plain `psycopg.connect` to the same container URL the conftest already builds, with `autocommit=True`).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_c4_billing_schema.py -v`
Expected: FAIL — enum/columns/table do not exist yet.

- [ ] **Step 3: Write the migration**

```sql
-- supabase/migrations/20260723000010_billing_tenants.sql
-- C4: billing state on tenants + the DB-authoritative tier->quota map.

create type public.subscription_status as enum (
  'trialing','active','past_due','canceled',
  'incomplete','incomplete_expired','unpaid','paused'
);

alter table public.tenants
  add column stripe_customer_id     text unique,
  add column stripe_subscription_id text,
  add column subscription_status    public.subscription_status,
  add column current_period_end     timestamptz,
  add column trial_ends_at          timestamptz;

create table public.plan_tiers (
  tier         text primary key,
  key_quota    integer not null check (key_quota > 0),
  display_name text not null,
  sort         integer not null default 0
);
insert into public.plan_tiers (tier, key_quota, display_name, sort) values
  ('starter', 5000,   'Starter', 1),
  ('growth',  25000,  'Growth',  2),
  ('scale',   100000, 'Scale',   3);

alter table public.plan_tiers enable row level security;
-- Public read (the marketing pricing page is unauthenticated); no public writes.
create policy plan_tiers_public_read on public.plan_tiers
  for select to anon, authenticated using (true);
grant select on public.plan_tiers to anon, authenticated, trax_app;
-- NOTE: no update/insert/delete grant to anon/authenticated/trax_app → writes
-- require the service role (bypasses RLS), which no member path ever uses.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_c4_billing_schema.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20260723000010_billing_tenants.sql services/agent-spine/tests/pg/test_c4_billing_schema.py services/agent-spine/tests/pg/conftest.py
git commit -m "feat(billing): migration 0010 — tenants billing columns + plan_tiers + status enum"
```

---

### Task 2: Migration 0011 — Stripe mirror tables (`products`/`prices`/`subscriptions`/`stripe_events`)

**Files:**
- Create: `supabase/migrations/20260723000011_billing_stripe_mirror.sql`
- Test: `services/agent-spine/tests/pg/test_c4_stripe_mirror.py`

**Interfaces:**
- Consumes: `subscription_status` enum, `plan_tiers`, `tenants` (Task 1).
- Produces: `products(id text pk, active bool, name, description, metadata jsonb)`; `prices(id text pk, product_id→products, active, unit_amount bigint, currency, interval, interval_count, trial_period_days, metadata jsonb)`; `subscriptions(id text pk, tenant_id→tenants, status subscription_status, price_id→prices, quantity, cancel_at_period_end, current_period_end, created, trial_end)`; `stripe_events(id text pk, type, received_at)`. RLS: products/prices public-read(active); subscriptions tenant-scoped read; all writes service-role only.

- [ ] **Step 1: Write the failing test**

```python
# services/agent-spine/tests/pg/test_c4_stripe_mirror.py
import psycopg
import pytest

def test_mirror_tables_exist(pg_admin_conn):
    tables = {r[0] for r in pg_admin_conn.execute(
        "select table_name from information_schema.tables "
        "where table_schema='public'").fetchall()}
    assert {"products","prices","subscriptions","stripe_events"} <= tables

def test_products_prices_public_read_no_write(pg_admin_conn):
    pg_admin_conn.execute(
        "insert into products (id,active,name) values ('prod_x',true,'Growth')")
    pg_admin_conn.execute(
        "insert into prices (id,product_id,active,unit_amount,currency,interval,"
        "metadata) values ('price_x','prod_x',true,29900,'usd','month',"
        "'{\"tier\":\"growth\"}'::jsonb)")
    pg_admin_conn.execute("set role anon")
    assert pg_admin_conn.execute("select count(*) from prices").fetchone()[0] == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg_admin_conn.execute("update prices set unit_amount=1 where id='price_x'")
    pg_admin_conn.execute("reset role")

def test_subscriptions_tenant_scoped_read(pg_admin_conn):
    # Two tenants; a subscription for tenant A; the trax_app role with tenant A's
    # claim sees it, with tenant B's claim does not.
    a = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c4a','A') returning id").fetchone()[0]
    b = pg_admin_conn.execute(
        "insert into tenants (slug,name) values ('c4b','B') returning id").fetchone()[0]
    pg_admin_conn.execute(
        "insert into subscriptions (id,tenant_id,status,price_id) "
        "values ('sub_x',%s,'active','price_x')", (a,))
    def _as_tenant(tid):
        pg_admin_conn.execute("set role trax_app")
        pg_admin_conn.execute(
            "select set_config('request.jwt.claims', %s, true)",
            (f'{{"tenant_id":"{tid}"}}',))
        n = pg_admin_conn.execute("select count(*) from subscriptions").fetchone()[0]
        pg_admin_conn.execute("reset role")
        return n
    assert _as_tenant(a) == 1
    assert _as_tenant(b) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_c4_stripe_mirror.py -v`
Expected: FAIL — tables do not exist.

- [ ] **Step 3: Write the migration**

```sql
-- supabase/migrations/20260723000011_billing_stripe_mirror.sql
-- C4: Stripe mirror (tenant-billed). Written ONLY by the stripe-webhook Edge
-- Function (service role); everyone else reads.

create table public.products (
  id text primary key, active boolean not null default true,
  name text, description text, metadata jsonb not null default '{}'
);
create table public.prices (
  id text primary key,
  product_id text references public.products (id),
  active boolean not null default true,
  unit_amount bigint, currency text,
  interval text check (interval in ('day','week','month','year')),
  interval_count integer default 1,
  trial_period_days integer,
  metadata jsonb not null default '{}'   -- metadata.tier binds to plan_tiers
);
create table public.subscriptions (
  id text primary key,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  status public.subscription_status not null,
  price_id text references public.prices (id),
  quantity integer default 1,
  cancel_at_period_end boolean not null default false,
  current_period_end timestamptz,
  created timestamptz not null default now(),
  trial_end timestamptz
);
create index subscriptions_tenant_id_idx on public.subscriptions (tenant_id);
create table public.stripe_events (
  id text primary key, type text, received_at timestamptz not null default now()
);

alter table public.products enable row level security;
alter table public.prices enable row level security;
alter table public.subscriptions enable row level security;
alter table public.stripe_events enable row level security;

create policy products_public_read on public.products
  for select to anon, authenticated using (active);
create policy prices_public_read on public.prices
  for select to anon, authenticated using (active);
create policy subscriptions_tenant_read on public.subscriptions
  for select to trax_app, authenticated
  using (tenant_id = (select public.current_tenant_id()));

grant select on public.products, public.prices to anon, authenticated, trax_app;
grant select on public.subscriptions to authenticated, trax_app;
-- No write grants → mirror writes require the service role only. stripe_events
-- has no policy/grant at all → service-role-only (webhook idempotency ledger).
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_c4_stripe_mirror.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20260723000011_billing_stripe_mirror.sql services/agent-spine/tests/pg/test_c4_stripe_mirror.py
git commit -m "feat(billing): migration 0011 — stripe mirror tables + RLS"
```

---

### Task 3: Migration 0012 — `leads` table + `create_tenant_for_current_user` RPC

**Files:**
- Create: `supabase/migrations/20260723000012_billing_leads_and_org_rpc.sql`
- Test: `services/agent-spine/tests/pg/test_c4_leads_and_org_rpc.py`

**Interfaces:**
- Consumes: `tenants`, `memberships` (C1).
- Produces: `leads(id uuid pk, name, email, company, message, source, created_at)` — anon insert-only; `public.create_tenant_for_current_user(p_name text) returns uuid` — SECURITY DEFINER, inserts a `tenants(plan_tier='trial')` + `memberships(owner)` for `auth.uid()`, unique slug, granted execute to `authenticated`.

- [ ] **Step 1: Write the failing test**

```python
# services/agent-spine/tests/pg/test_c4_leads_and_org_rpc.py
import psycopg
import pytest
import uuid

def test_leads_anon_insert_only(pg_admin_conn):
    pg_admin_conn.execute("set role anon")
    pg_admin_conn.execute(
        "insert into leads (name,email,message,source) "
        "values ('X','x@y.z','hi','pricing')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pg_admin_conn.execute("select count(*) from leads")  # no read
    pg_admin_conn.execute("reset role")

def test_create_tenant_for_current_user(pg_admin_conn):
    uid = str(uuid.uuid4())
    pg_admin_conn.execute("set role authenticated")
    pg_admin_conn.execute(
        "select set_config('request.jwt.claims', %s, true)", (f'{{"sub":"{uid}"}}',))
    tid = pg_admin_conn.execute(
        "select public.create_tenant_for_current_user('Acme Air')").fetchone()[0]
    pg_admin_conn.execute("reset role")
    row = pg_admin_conn.execute(
        "select plan_tier from tenants where id=%s", (tid,)).fetchone()
    assert row[0] == "trial"
    mem = pg_admin_conn.execute(
        "select role from memberships where tenant_id=%s and user_id=%s",
        (tid, uid)).fetchone()
    assert mem[0] == "owner"

def test_create_tenant_unique_slug(pg_admin_conn):
    uid = str(uuid.uuid4())
    pg_admin_conn.execute("set role authenticated")
    pg_admin_conn.execute(
        "select set_config('request.jwt.claims', %s, true)", (f'{{"sub":"{uid}"}}',))
    t1 = pg_admin_conn.execute(
        "select public.create_tenant_for_current_user('Dup Name')").fetchone()[0]
    t2 = pg_admin_conn.execute(
        "select public.create_tenant_for_current_user('Dup Name')").fetchone()[0]
    pg_admin_conn.execute("reset role")
    slugs = pg_admin_conn.execute(
        "select slug from tenants where id in (%s,%s)", (t1, t2)).fetchall()
    assert len({s[0] for s in slugs}) == 2  # slugs differ
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_c4_leads_and_org_rpc.py -v`
Expected: FAIL — `leads`/RPC absent.

- [ ] **Step 3: Write the migration**

```sql
-- supabase/migrations/20260723000012_billing_leads_and_org_rpc.sql
-- C4: marketing leads (anon insert-only) + self-serve org creation.

create table public.leads (
  id uuid primary key default gen_random_uuid(),
  name text, email text, company text, message text, source text,
  created_at timestamptz not null default now()
);
alter table public.leads enable row level security;
create policy leads_anon_insert on public.leads
  for insert to anon, authenticated with check (true);
grant insert on public.leads to anon, authenticated;
-- No select policy/grant → nobody reads leads via the API (team reads in Supabase).

-- Self-serve org creation: C1 RLS blocks direct member tenants-inserts; this
-- scoped create-for-self function is the sanctioned exception.
create function public.create_tenant_for_current_user(p_name text)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_uid  uuid := (auth.jwt()->>'sub')::uuid;
  v_base text := regexp_replace(lower(p_name), '[^a-z0-9]+', '-', 'g');
  v_slug text;
  v_id   uuid;
begin
  if v_uid is null then raise exception 'no authenticated user'; end if;
  v_base := trim(both '-' from v_base);
  if length(v_base) < 2 then v_base := 'org'; end if;
  v_slug := left(v_base, 55) || '-' || substr(gen_random_uuid()::text, 1, 6);
  insert into public.tenants (slug, name, plan_tier)
    values (v_slug, p_name, 'trial') returning id into v_id;
  insert into public.memberships (user_id, tenant_id, role)
    values (v_uid, v_id, 'owner');
  return v_id;
end;
$$;
grant execute on function public.create_tenant_for_current_user(text) to authenticated;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg/test_c4_leads_and_org_rpc.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full pg suite (regression) + commit**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/pg -q`
Expected: all prior pg tests still pass + the new C4 schema tests.

```bash
git add supabase/migrations/20260723000012_billing_leads_and_org_rpc.sql services/agent-spine/tests/pg/test_c4_leads_and_org_rpc.py
git commit -m "feat(billing): migration 0012 — leads + create_tenant_for_current_user RPC"
```

---

## GROUP 2 — Edge Functions (Deno)

> Deno functions live in `supabase/functions/`. Tests run with `deno test --allow-env --allow-net` from `supabase/functions/`. Stripe is imported via `npm:stripe@^16`; the Supabase client via `jsr:@supabase/supabase-js@2`. Each function reads secrets from `Deno.env`.

### Task 4: `_shared` clients + `create-checkout-session`

**Files:**
- Create: `supabase/functions/_shared/stripe.ts`, `_shared/supabase.ts`, `_shared/cors.ts`
- Create: `supabase/functions/create-checkout-session/index.ts`, `.../deno.test.ts`
- Create: `supabase/functions/deno.json` (import map / lint config)

**Interfaces:**
- Produces: `getStripe(): Stripe` (from `STRIPE_SECRET_KEY`); `getServiceClient(): SupabaseClient` (from `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`); `corsHeaders`; the `create-checkout-session` HTTP handler `handler(req: Request, deps): Promise<Response>` where `deps = { stripe, admin }` is injectable for tests.
- Consumes: `tenants.stripe_customer_id` (Task 1), JWT `tenant_id`/`sub` claims (C2 auth), `memberships` (C1).

- [ ] **Step 1: Write the failing test**

```ts
// supabase/functions/create-checkout-session/deno.test.ts
import { assertEquals } from "jsr:@std/assert";
import { handler } from "./index.ts";

// Minimal fakes: an admin client that returns a fixed tenant/membership, and a
// Stripe stub whose checkout.sessions.create echoes its args.
function fakeDeps({ role = "owner", customerId = null } = {}) {
  const admin = {
    from(table: string) {
      return {
        select: () => ({ eq: () => ({ eq: () => ({ maybeSingle: () =>
          table === "memberships"
            ? { data: role ? { role } : null }
            : { data: { stripe_customer_id: customerId, id: "T1" } } }) }),
          maybeSingle: () => ({ data: { stripe_customer_id: customerId, id: "T1" } }) }),
        update: () => ({ eq: () => ({ error: null }) }),
      };
    },
  };
  const created: any = {};
  const stripe = {
    customers: { create: async (a: any) => { created.customer = a; return { id: "cus_new" }; } },
    checkout: { sessions: { create: async (a: any) => { created.session = a; return { url: "https://stripe/checkout" }; } } },
  };
  return { admin, stripe, created };
}

function req(body: unknown, claims: Record<string, unknown>) {
  // The handler trusts an already-verified JWT: it decodes claims from a header
  // the Supabase functions runtime sets (x-user-claims) OR verifies the bearer.
  return new Request("http://x", {
    method: "POST",
    headers: { "content-type": "application/json", "x-test-claims": JSON.stringify(claims) },
    body: JSON.stringify(body),
  });
}

Deno.test("owner gets a checkout url; a new customer is created and stored", async () => {
  const deps = fakeDeps({ role: "owner", customerId: null });
  const res = await handler(req({ price_id: "price_growth" },
    { sub: "u1", tenant_id: "T1", tenant_role: "owner" }), deps);
  assertEquals(res.status, 200);
  assertEquals((await res.json()).url, "https://stripe/checkout");
  assertEquals(deps.created.session.subscription_data.trial_period_days, 14);
  assertEquals(deps.created.session.payment_method_collection, "always");
});

Deno.test("non-owner is 403", async () => {
  const deps = fakeDeps({ role: "planner" });
  const res = await handler(req({ price_id: "price_growth" },
    { sub: "u1", tenant_id: "T1", tenant_role: "planner" }), deps);
  assertEquals(res.status, 403);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd supabase/functions && deno test create-checkout-session/deno.test.ts --allow-env --allow-net`
Expected: FAIL — `./index.ts` has no `handler` export.

- [ ] **Step 3: Write `_shared` + the handler**

```ts
// supabase/functions/_shared/cors.ts
export const corsHeaders = {
  "Access-Control-Allow-Origin": Deno.env.get("APP_ORIGIN") ?? "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status, headers: { ...corsHeaders, "content-type": "application/json" } });
```
```ts
// supabase/functions/_shared/stripe.ts
import Stripe from "npm:stripe@^16";
export const getStripe = () =>
  new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!, { apiVersion: "2025-03-31.basil" });
```
```ts
// supabase/functions/_shared/supabase.ts
import { createClient, type SupabaseClient } from "jsr:@supabase/supabase-js@2";
export const getServiceClient = (): SupabaseClient =>
  createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
    { auth: { persistSession: false } });
```
```ts
// supabase/functions/create-checkout-session/index.ts
import { corsHeaders, json } from "../_shared/cors.ts";
import { getStripe } from "../_shared/stripe.ts";
import { getServiceClient } from "../_shared/supabase.ts";

// In production the Supabase runtime verifies the bearer JWT (verify_jwt=true)
// and exposes claims; in tests we inject via x-test-claims. Real deploys read
// the verified claims from the Authorization bearer.
function claimsOf(req: Request): Record<string, any> | null {
  const t = req.headers.get("x-test-claims");
  if (t) return JSON.parse(t);
  const auth = req.headers.get("Authorization")?.replace("Bearer ", "");
  if (!auth) return null;
  try { return JSON.parse(atob(auth.split(".")[1])); } catch { return null; }
}

export async function handler(req: Request, deps?: { stripe: any; admin: any }) {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  const claims = claimsOf(req);
  if (!claims?.sub || !claims?.tenant_id) return json({ error: "unauthenticated" }, 401);
  if (claims.tenant_role !== "owner") return json({ error: "owner required" }, 403);

  const { price_id } = await req.json();
  if (!price_id) return json({ error: "price_id required" }, 400);

  const stripe = deps?.stripe ?? getStripe();
  const admin = deps?.admin ?? getServiceClient();

  const { data: tenant } = await admin.from("tenants")
    .select("id, stripe_customer_id").eq("id", claims.tenant_id).maybeSingle();
  if (!tenant) return json({ error: "tenant not found" }, 404);

  let customerId = tenant.stripe_customer_id;
  if (!customerId) {
    const cust = await stripe.customers.create({ metadata: { tenant_id: tenant.id } });
    customerId = cust.id;
    await admin.from("tenants").update({ stripe_customer_id: customerId }).eq("id", tenant.id);
  }

  const appOrigin = Deno.env.get("APP_ORIGIN") ?? "http://localhost:5173";
  const session = await stripe.checkout.sessions.create({
    mode: "subscription",
    customer: customerId,
    line_items: [{ price: price_id, quantity: 1 }],
    subscription_data: { trial_period_days: 14 },
    payment_method_collection: "always",
    metadata: { tenant_id: tenant.id },
    success_url: `${appOrigin}/#/billing?checkout=success`,
    cancel_url: `${appOrigin}/#/billing?checkout=cancel`,
  });
  return json({ url: session.url });
}

Deno.serve((req) => handler(req));
```
```json
// supabase/functions/deno.json
{ "lint": { "rules": { "tags": ["recommended"] } } }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd supabase/functions && deno test create-checkout-session/deno.test.ts --allow-env --allow-net`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add supabase/functions/_shared supabase/functions/create-checkout-session supabase/functions/deno.json
git commit -m "feat(billing): create-checkout-session edge function + shared clients"
```

---

### Task 5: `create-portal-link`

**Files:**
- Create: `supabase/functions/create-portal-link/index.ts`, `.../deno.test.ts`

**Interfaces:**
- Produces: `handler(req, deps?)` — owner-gated; loads `tenants.stripe_customer_id`; `stripe.billingPortal.sessions.create({customer, return_url})`; returns `{url}`. 403 non-owner; 409 if no customer yet.
- Consumes: `_shared/*` (Task 4).

- [ ] **Step 1: Write the failing test**

```ts
// supabase/functions/create-portal-link/deno.test.ts
import { assertEquals } from "jsr:@std/assert";
import { handler } from "./index.ts";

function deps(customerId: string | null) {
  return {
    admin: { from: () => ({ select: () => ({ eq: () => ({ maybeSingle: () =>
      ({ data: { stripe_customer_id: customerId } }) }) }) }) },
    stripe: { billingPortal: { sessions: { create: async () =>
      ({ url: "https://stripe/portal" }) } } },
  };
}
const req = (claims: Record<string, unknown>) => new Request("http://x",
  { method: "POST", headers: { "x-test-claims": JSON.stringify(claims) } });

Deno.test("owner with a customer gets a portal url", async () => {
  const res = await handler(req({ sub: "u1", tenant_id: "T1", tenant_role: "owner" }),
    deps("cus_1"));
  assertEquals(res.status, 200);
  assertEquals((await res.json()).url, "https://stripe/portal");
});
Deno.test("no customer yet -> 409", async () => {
  const res = await handler(req({ sub: "u1", tenant_id: "T1", tenant_role: "owner" }),
    deps(null));
  assertEquals(res.status, 409);
});
Deno.test("non-owner -> 403", async () => {
  const res = await handler(req({ sub: "u1", tenant_id: "T1", tenant_role: "viewer" }),
    deps("cus_1"));
  assertEquals(res.status, 403);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd supabase/functions && deno test create-portal-link/deno.test.ts --allow-env --allow-net`
Expected: FAIL — no `handler`.

- [ ] **Step 3: Write the handler**

```ts
// supabase/functions/create-portal-link/index.ts
import { corsHeaders, json } from "../_shared/cors.ts";
import { getStripe } from "../_shared/stripe.ts";
import { getServiceClient } from "../_shared/supabase.ts";

function claimsOf(req: Request): Record<string, any> | null {
  const t = req.headers.get("x-test-claims");
  if (t) return JSON.parse(t);
  const auth = req.headers.get("Authorization")?.replace("Bearer ", "");
  if (!auth) return null;
  try { return JSON.parse(atob(auth.split(".")[1])); } catch { return null; }
}

export async function handler(req: Request, deps?: { stripe: any; admin: any }) {
  if (req.method === "OPTIONS") return new Response("ok", { headers: corsHeaders });
  const claims = claimsOf(req);
  if (!claims?.tenant_id) return json({ error: "unauthenticated" }, 401);
  if (claims.tenant_role !== "owner") return json({ error: "owner required" }, 403);

  const admin = deps?.admin ?? getServiceClient();
  const { data: tenant } = await admin.from("tenants")
    .select("stripe_customer_id").eq("id", claims.tenant_id).maybeSingle();
  if (!tenant?.stripe_customer_id) return json({ error: "no customer" }, 409);

  const stripe = deps?.stripe ?? getStripe();
  const appOrigin = Deno.env.get("APP_ORIGIN") ?? "http://localhost:5173";
  const portal = await stripe.billingPortal.sessions.create({
    customer: tenant.stripe_customer_id, return_url: `${appOrigin}/#/billing`,
  });
  return json({ url: portal.url });
}

Deno.serve((req) => handler(req));
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd supabase/functions && deno test create-portal-link/deno.test.ts --allow-env --allow-net`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add supabase/functions/create-portal-link
git commit -m "feat(billing): create-portal-link edge function"
```

---

### Task 6: `stripe-webhook` (mirror + tenant sync, idempotent) + `config.toml`

**Files:**
- Create: `supabase/functions/stripe-webhook/sync.ts` (pure sync logic, unit-testable), `.../index.ts` (HTTP + signature), `.../deno.test.ts`
- Create: `supabase/config.toml`

**Interfaces:**
- Produces: `applyEvent(admin, event): Promise<void>` — upserts products/prices/subscriptions and syncs `tenants` per the spec §2.7 write contract; `handler(req, deps?)` — verifies the Stripe signature, dedups on `stripe_events`, calls `applyEvent`.
- Consumes: mirror tables + `plan_tiers` (Tasks 1–2), `_shared/*` (Task 4).

- [ ] **Step 1: Write the failing test**

```ts
// supabase/functions/stripe-webhook/deno.test.ts
import { assertEquals } from "jsr:@std/assert";
import { applyEvent } from "./sync.ts";

// A fake admin client recording upserts + tenant updates, with a plan_tiers lookup.
function fakeAdmin() {
  const calls: any[] = [];
  const planQuota: Record<string, number> = { growth: 25000, scale: 100000, starter: 5000 };
  return {
    calls,
    from(table: string) {
      return {
        upsert: (row: any) => { calls.push({ table, op: "upsert", row }); return { error: null }; },
        update: (row: any) => ({ eq: (_c: string, v: string) => {
          calls.push({ table, op: "update", row, id: v }); return { error: null }; } }),
        select: () => ({ eq: (_c: string, v: string) => ({ maybeSingle: () =>
          ({ data: table === "plan_tiers" ? { key_quota: planQuota[v] ?? null } : null }) }) }),
      };
    },
  };
}

Deno.test("subscription.updated syncs tenants plan_tier + key_quota from price.metadata.tier", async () => {
  const admin = fakeAdmin();
  await applyEvent(admin as any, {
    type: "customer.subscription.updated",
    data: { object: {
      id: "sub_1", status: "active", metadata: { tenant_id: "T1" },
      items: { data: [{ price: { id: "price_g", metadata: { tier: "growth" } } }] },
      current_period_end: 1893456000, trial_end: null, cancel_at_period_end: false,
    } },
  });
  const tenantUpdate = admin.calls.find((c) => c.table === "tenants" && c.op === "update");
  assertEquals(tenantUpdate.row.plan_tier, "growth");
  assertEquals(tenantUpdate.row.key_quota, 25000);
  assertEquals(tenantUpdate.row.subscription_status, "active");
});
```

Add a second test file `stripe-webhook/handler.test.ts` for idempotency + signature:

```ts
// supabase/functions/stripe-webhook/handler.test.ts
import { assertEquals } from "jsr:@std/assert";
import { handler } from "./index.ts";

function deps({ seen = new Set<string>() } = {}) {
  const applied: string[] = [];
  const admin = { from: (t: string) => ({
    insert: (r: any) => { if (t === "stripe_events") {
      if (seen.has(r.id)) return { error: { code: "23505" } }; seen.add(r.id); }
      return { error: null }; } }) };
  // Stub Stripe signature verification: valid unless body contains "BAD".
  const stripe = { webhooks: { constructEventAsync: async (body: string) => {
    if (body.includes("BAD")) throw new Error("bad sig");
    return { id: JSON.parse(body).id, type: "ping", data: { object: {} } }; } } };
  return { admin, stripe, applyEvent: async (_a: any, e: any) => { applied.push(e.id); }, applied };
}
const req = (body: string) => new Request("http://x",
  { method: "POST", headers: { "stripe-signature": "sig" }, body });

Deno.test("bad signature -> 400, no apply", async () => {
  const d = deps();
  const res = await handler(req('{"id":"evt_BAD"}'), d);
  assertEquals(res.status, 400);
  assertEquals(d.applied.length, 0);
});
Deno.test("duplicate event.id -> processed once", async () => {
  const d = deps();
  await handler(req('{"id":"evt_1"}'), d);
  await handler(req('{"id":"evt_1"}'), d);
  assertEquals(d.applied.length, 1);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd supabase/functions && deno test stripe-webhook/ --allow-env --allow-net`
Expected: FAIL — `sync.ts`/`index.ts` absent.

- [ ] **Step 3: Write `sync.ts`, `index.ts`, `config.toml`**

```ts
// supabase/functions/stripe-webhook/sync.ts
// Pure mirror/tenant sync. No HTTP, no signature — unit-testable.
export async function applyEvent(admin: any, event: any): Promise<void> {
  const o = event.data.object;
  switch (event.type) {
    case "product.created": case "product.updated": case "product.deleted":
      await admin.from("products").upsert({
        id: o.id, active: o.active ?? true, name: o.name ?? null,
        description: o.description ?? null, metadata: o.metadata ?? {} });
      return;
    case "price.created": case "price.updated": case "price.deleted":
      await admin.from("prices").upsert({
        id: o.id, product_id: o.product, active: o.active ?? true,
        unit_amount: o.unit_amount ?? null, currency: o.currency ?? null,
        interval: o.recurring?.interval ?? null,
        interval_count: o.recurring?.interval_count ?? 1,
        trial_period_days: o.recurring?.trial_period_days ?? null,
        metadata: o.metadata ?? {} });
      return;
    case "customer.subscription.created":
    case "customer.subscription.updated":
    case "customer.subscription.deleted": {
      const price = o.items?.data?.[0]?.price;
      const tenantId = o.metadata?.tenant_id;
      await admin.from("subscriptions").upsert({
        id: o.id, tenant_id: tenantId, status: o.status, price_id: price?.id ?? null,
        quantity: o.items?.data?.[0]?.quantity ?? 1,
        cancel_at_period_end: o.cancel_at_period_end ?? false,
        current_period_end: o.current_period_end
          ? new Date(o.current_period_end * 1000).toISOString() : null,
        trial_end: o.trial_end ? new Date(o.trial_end * 1000).toISOString() : null });
      if (tenantId) {
        const tier = price?.metadata?.tier ?? null;
        let keyQuota: number | null = null, planTier: string | null = null;
        if (tier) {
          const { data } = await admin.from("plan_tiers")
            .select("key_quota").eq("tier", tier).maybeSingle();
          keyQuota = data?.key_quota ?? null; planTier = tier;
        }
        const patch: Record<string, unknown> = {
          subscription_status: o.status, stripe_subscription_id: o.id,
          current_period_end: o.current_period_end
            ? new Date(o.current_period_end * 1000).toISOString() : null,
          trial_ends_at: o.trial_end
            ? new Date(o.trial_end * 1000).toISOString() : null };
        if (planTier) { patch.plan_tier = planTier; patch.key_quota = keyQuota; }
        await admin.from("tenants").update(patch).eq("id", tenantId);
      }
      return;
    }
    case "checkout.session.completed":
      if (o.metadata?.tenant_id && o.customer) {
        await admin.from("tenants")
          .update({ stripe_customer_id: o.customer }).eq("id", o.metadata.tenant_id);
      }
      return;
    default: return; // ack-and-ignore
  }
}
```
```ts
// supabase/functions/stripe-webhook/index.ts
import { getStripe } from "../_shared/stripe.ts";
import { getServiceClient } from "../_shared/supabase.ts";
import { applyEvent as realApply } from "./sync.ts";

export async function handler(
  req: Request,
  deps?: { admin: any; stripe: any; applyEvent?: (a: any, e: any) => Promise<void> },
) {
  const admin = deps?.admin ?? getServiceClient();
  const stripe = deps?.stripe ?? getStripe();
  const apply = deps?.applyEvent ?? realApply;

  const body = await req.text();
  const sig = req.headers.get("stripe-signature") ?? "";
  const secret = Deno.env.get("STRIPE_WEBHOOK_SIGNING_SECRET") ?? "test";
  let event: any;
  try {
    event = await stripe.webhooks.constructEventAsync(body, sig, secret);
  } catch {
    return new Response("bad signature", { status: 400 });
  }

  // Idempotency: insert event.id; a unique-violation means we already processed it.
  const { error } = await admin.from("stripe_events").insert({ id: event.id, type: event.type });
  if (error) {
    if (error.code === "23505") return new Response("dup", { status: 200 });
    return new Response("event log error", { status: 500 });
  }
  try {
    await apply(admin, event);
  } catch (e) {
    return new Response(`apply error: ${e}`, { status: 500 }); // Stripe retries
  }
  return new Response("ok", { status: 200 });
}

Deno.serve((req) => handler(req));
```
```toml
# supabase/config.toml
project_id = "aeronta-inventory"

[functions.create-checkout-session]
verify_jwt = true
[functions.create-portal-link]
verify_jwt = true
[functions.stripe-webhook]
verify_jwt = false
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd supabase/functions && deno test stripe-webhook/ --allow-env --allow-net`
Expected: PASS (3 tests across the two files).

- [ ] **Step 5: Commit**

```bash
git add supabase/functions/stripe-webhook supabase/config.toml
git commit -m "feat(billing): stripe-webhook edge function (idempotent mirror + tenant sync)"
```

---

## GROUP 3 — BFF (write-gate + billing read)

### Task 7: `require_active_subscription` 402 write-gate in `AuthMiddleware`

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/auth.py` (add `subscription_status_for` param + the write-gate after the role floor, ~line 115-121)
- Modify: `services/agent-spine/src/trax_io_spine/bff/app.py` (`create_planner_app` gains `subscription_status_for` param, passed to `AuthMiddleware`)
- Modify: `services/agent-spine/src/trax_io_spine/bff/asgi.py` (wire a pool-backed reader in `DATABASE_URL` mode)
- Test: `services/agent-spine/tests/bff/test_c4_write_gate.py`

**Interfaces:**
- Consumes: `AuthMiddleware(app, verifier, tenant_uuids)` (existing), `request.state.claims.tenant_id` (existing).
- Produces: `AuthMiddleware(app, verifier, tenant_uuids, subscription_status_for=None)` where `subscription_status_for: Callable[[str], str | None]` maps a tenant uuid → status; on write methods, after the role floor, returns **402** unless status ∈ `{"trialing","active","past_due"}`. `None` gate ⇒ no gating (dev/in-memory unchanged). `create_planner_app(..., subscription_status_for=None)`.

- [ ] **Step 1: Write the failing test**

```python
# services/agent-spine/tests/bff/test_c4_write_gate.py
from datetime import UTC, datetime, timedelta
from pathlib import Path
import jwt
import pytest
from fastapi.testclient import TestClient
from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.bff.store import PlannerStore

TENANT_UUID = "753b64bd-9885-4639-b116-8f2c5c497232"
_SAMPLE = Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
SECRET = "unit-test-secret-0123456789abcdef"

class _V:
    def __init__(self): self._v = HsVerifier(SECRET)
    def verify(self, t): return self._v.verify(t)

def _tok(role="planner"):
    now = datetime.now(UTC)
    return jwt.encode({"sub":"u1","aud":"authenticated","iat":now,
        "exp":now+timedelta(minutes=5),"tenant_id":TENANT_UUID,"tenant_role":role},
        SECRET, algorithm="HS256")

def _client(status):
    store = PlannerStore.from_extract(tenant_id="aeronta-demo", extract_dir=str(_SAMPLE),
        now=datetime(2026,4,1,tzinfo=UTC))
    app = create_planner_app({"aeronta-demo": store}, verifier=_V(),
        tenant_uuids={"aeronta-demo": TENANT_UUID},
        subscription_status_for=lambda _uuid: status)
    return TestClient(app)

@pytest.mark.parametrize("status,code", [
    ("trialing", 404), ("active", 404), ("past_due", 404),   # write reaches handler (404 unknown rec)
    ("canceled", 402), ("unpaid", 402), ("paused", 402),
    ("incomplete", 402), (None, 402),
])
def test_write_gate_matrix(status, code):
    r = _client(status).post(
        "/v1/tenants/aeronta-demo/recommendations/nope/approve",
        headers={"Authorization": f"Bearer {_tok('planner')}"})
    assert r.status_code == code

def test_reads_never_gated_even_when_canceled():
    r = _client("canceled").get("/v1/tenants/aeronta-demo/recommendations",
        headers={"Authorization": f"Bearer {_tok('viewer')}"})
    assert r.status_code == 200

def test_no_gate_callable_means_no_gating():
    # Omitting subscription_status_for keeps the in-memory/dev behavior.
    store = PlannerStore.from_extract(tenant_id="aeronta-demo", extract_dir=str(_SAMPLE),
        now=datetime(2026,4,1,tzinfo=UTC))
    app = create_planner_app({"aeronta-demo": store}, verifier=_V(),
        tenant_uuids={"aeronta-demo": TENANT_UUID})
    r = TestClient(app).post("/v1/tenants/aeronta-demo/recommendations/nope/approve",
        headers={"Authorization": f"Bearer {_tok('planner')}"})
    assert r.status_code == 404  # reaches handler, not 402
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr pytest tests/bff/test_c4_write_gate.py -v`
Expected: FAIL — `create_planner_app` has no `subscription_status_for`.

- [ ] **Step 3: Implement**

In `auth.py`, extend `__init__` and the write branch (the existing role-floor block at ~115-121):

```python
# auth.py — __init__ signature
def __init__(self, app, verifier, tenant_uuids: dict[str, str] | None = None,
             subscription_status_for=None) -> None:
    self.app = app
    self.verifier = verifier
    self.tenant_uuids = tenant_uuids or {}
    self.subscription_status_for = subscription_status_for  # Callable[[str], str|None] | None

# auth.py — inside __call__, right AFTER the existing role-floor 403 block:
_ACTIVE = {"trialing", "active", "past_due"}
# ... existing: if method not in (GET/HEAD/OPTIONS) and role is viewer-or-below -> 403 ...
if method not in ("GET", "HEAD", "OPTIONS") and self.subscription_status_for is not None:
    status = self.subscription_status_for(expected)  # expected = tenant uuid for this slug
    if status not in _ACTIVE:
        return await _reject(402, "subscription inactive")(scope, receive, send)
```

In `app.py`:

```python
# create_planner_app signature: add subscription_status_for=None
def create_planner_app(stores, *, verifier=None, tenant_uuids=None, admin_api=None,
                       members_stores=None, upload_minter=None, ingest_stores=None,
                       subscription_status_for=None):
    ...
    if verifier is not None:
        app.add_middleware(AuthMiddleware, verifier=verifier, tenant_uuids=tenant_uuids,
                           subscription_status_for=subscription_status_for)
```

In `asgi.py`, in the `DATABASE_URL` branch, build a pool-backed reader (cached per uuid is unnecessary — a single indexed read):

```python
# asgi.py (DATABASE_URL branch) — after `pool = make_pool(database_url)`
def _sub_status_for(tenant_uuid: str) -> str | None:
    with pool.connection() as c:
        row = c.execute(
            "select subscription_status::text from tenants where id = %s::uuid",
            (tenant_uuid,)).fetchone()
    return row[0] if row else None
# ...pass subscription_status_for=_sub_status_for to create_planner_app(...)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr pytest tests/bff/test_c4_write_gate.py -v`
Expected: PASS (10 params + 2 = 12).

Then the existing auth-middleware suite (regression): `pytest tests/bff/test_auth_middleware.py -q` → all pass (the gate is inert when `subscription_status_for is None`, which those tests use).

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/bff/auth.py services/agent-spine/src/trax_io_spine/bff/app.py services/agent-spine/src/trax_io_spine/bff/asgi.py services/agent-spine/tests/bff/test_c4_write_gate.py
git commit -m "feat(billing): BFF 402 write-gate for inactive subscriptions (reads never gated)"
```

---

### Task 8: `GET /v1/tenants/{t}/billing` (status + usage read)

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/bff/billing.py`
- Modify: `services/agent-spine/src/trax_io_spine/bff/app.py` (add the route; thread a `billing_reader` like `subscription_status_for`)
- Modify: `services/agent-spine/src/trax_io_spine/bff/asgi.py` (wire the pool-backed reader)
- Test: `services/agent-spine/tests/bff/test_c4_billing_read.py`, `services/agent-spine/tests/pg/test_c4_billing_read_pg.py`

**Interfaces:**
- Produces: `BillingSummary` (pydantic: `plan_tier: str`, `subscription_status: str | None`, `key_quota: int`, `keys_used: int`, `current_period_end: datetime | None`, `trial_ends_at: datetime | None`); `billing_summary(conn, tenant_uuid) -> BillingSummary` (reads `tenants` + `count(part_keys)`); route `GET {base}/billing` returns it. `create_planner_app(..., billing_reader=None)` where `billing_reader: Callable[[str], BillingSummary]`.
- Consumes: `tenants` billing columns (Task 1), `part_keys` (C1).

- [ ] **Step 1: Write the failing test** (route-level, in-memory reader)

```python
# services/agent-spine/tests/bff/test_c4_billing_read.py
from datetime import UTC, datetime, timedelta
from pathlib import Path
import jwt
from fastapi.testclient import TestClient
from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.auth import HsVerifier
from trax_io_spine.bff.billing import BillingSummary
from trax_io_spine.bff.store import PlannerStore

TENANT_UUID = "753b64bd-9885-4639-b116-8f2c5c497232"
_SAMPLE = Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
SECRET = "unit-test-secret-0123456789abcdef"
class _V:
    def __init__(self): self._v = HsVerifier(SECRET)
    def verify(self, t): return self._v.verify(t)
def _tok(role="planner"):
    now = datetime.now(UTC)
    return jwt.encode({"sub":"u1","aud":"authenticated","iat":now,"exp":now+timedelta(minutes=5),
        "tenant_id":TENANT_UUID,"tenant_role":role}, SECRET, algorithm="HS256")

def test_billing_endpoint_returns_summary():
    store = PlannerStore.from_extract(tenant_id="aeronta-demo", extract_dir=str(_SAMPLE),
        now=datetime(2026,4,1,tzinfo=UTC))
    summary = BillingSummary(plan_tier="growth", subscription_status="active",
        key_quota=25000, keys_used=42, current_period_end=None, trial_ends_at=None)
    app = create_planner_app({"aeronta-demo": store}, verifier=_V(),
        tenant_uuids={"aeronta-demo": TENANT_UUID},
        billing_reader=lambda _uuid: summary)
    r = TestClient(app).get("/v1/tenants/aeronta-demo/billing",
        headers={"Authorization": f"Bearer {_tok('planner')}"})
    assert r.status_code == 200
    body = r.json()
    assert body["plan_tier"] == "growth" and body["keys_used"] == 42
```

And a pg-level test for `billing_summary`:

```python
# services/agent-spine/tests/pg/test_c4_billing_read_pg.py
from trax_io_spine.bff.billing import billing_summary

def test_billing_summary_reads_tenant_and_counts_keys(pg_admin_conn):
    tid = pg_admin_conn.execute(
        "insert into tenants (slug,name,plan_tier,key_quota,subscription_status) "
        "values ('c4bill','B','growth',25000,'active') returning id").fetchone()[0]
    pg_admin_conn.execute(
        "insert into part_keys (tenant_id,pn,location,key_stats) "
        "values (%s,'P1','JFK','{}'::jsonb),(%s,'P2','JFK','{}'::jsonb)", (tid, tid))
    s = billing_summary(pg_admin_conn, str(tid))
    assert s.plan_tier == "growth" and s.key_quota == 25000
    assert s.subscription_status == "active" and s.keys_used == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/bff/test_c4_billing_read.py tests/pg/test_c4_billing_read_pg.py -v`
Expected: FAIL — `bff.billing` module absent.

- [ ] **Step 3: Implement**

```python
# services/agent-spine/src/trax_io_spine/bff/billing.py
from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel

class BillingSummary(BaseModel):
    plan_tier: str
    subscription_status: str | None
    key_quota: int
    keys_used: int
    current_period_end: datetime | None
    trial_ends_at: datetime | None

def billing_summary(conn, tenant_uuid: str) -> BillingSummary:
    row = conn.execute(
        "select plan_tier, subscription_status::text, key_quota, "
        "current_period_end, trial_ends_at from tenants where id = %s::uuid",
        (tenant_uuid,)).fetchone()
    if row is None:
        raise ValueError(f"unknown tenant {tenant_uuid}")
    used = conn.execute(
        "select count(*) from part_keys where tenant_id = %s::uuid",
        (tenant_uuid,)).fetchone()[0]
    return BillingSummary(
        plan_tier=row[0], subscription_status=row[1], key_quota=row[2],
        keys_used=used, current_period_end=row[3], trial_ends_at=row[4])
```

In `app.py` add the route (thread `billing_reader` like `subscription_status_for`):

```python
# create_planner_app(..., billing_reader=None)
@app.get(base + "/billing")
def billing(tenant_id: str, request: Request):
    if billing_reader is None:
        raise HTTPException(status_code=503, detail="billing not configured")
    uuid = app.state.tenant_uuids.get(tenant_id)
    if uuid is None:
        raise HTTPException(status_code=404, detail=f"unknown tenant {tenant_id}")
    return billing_reader(uuid)
```

In `asgi.py` (DATABASE_URL branch), wire:

```python
from trax_io_spine.bff.billing import billing_summary
def _billing_reader(tenant_uuid: str):
    with pool.connection() as c:
        return billing_summary(c, tenant_uuid)
# pass billing_reader=_billing_reader to create_planner_app(...)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev --extra bff --extra bvr --extra pg-test pytest tests/bff/test_c4_billing_read.py tests/pg/test_c4_billing_read_pg.py -v`
Expected: PASS. Then the whole bff+pg suite (regression): `pytest tests/bff tests/pg -q`.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/bff/billing.py services/agent-spine/src/trax_io_spine/bff/app.py services/agent-spine/src/trax_io_spine/bff/asgi.py services/agent-spine/tests/bff/test_c4_billing_read.py services/agent-spine/tests/pg/test_c4_billing_read_pg.py
git commit -m "feat(billing): GET /v1/tenants/{t}/billing status+usage read"
```

---

## GROUP 4 — apps/web (signup + billing surface)

> Vite/React/Tailwind/shadcn + TanStack Query. Tests are Vitest (`cd apps/web && npm test`). The app uses `HashRouter`; the Supabase client is `apps/web/src/lib/auth/supabase.ts` (`supabase` may be `null` in auth-disabled dev — every consumer must degrade). BFF reads go through `lib/api/client.ts`; Stripe actions call the Edge Functions directly at `${VITE_SUPABASE_URL}/functions/v1/<name>` with the user's bearer token.

### Task 9: billing API client + `useSubscription` hook + edge-function callers

**Files:**
- Create: `apps/web/src/lib/api/billing.ts`, `apps/web/src/lib/api/useSubscription.ts`
- Create: `apps/web/src/lib/api/billing.test.ts`

**Interfaces:**
- Consumes: BFF `GET /v1/tenants/{t}/billing` (Task 8), `create-checkout-session`/`create-portal-link` (Tasks 4–5), `apiFetch`/base-URL helper in `lib/api/client.ts`, `supabase` from `lib/auth/supabase.ts`.
- Produces: `getBilling(tenant: string): Promise<BillingSummary>`; `createCheckoutSession(tenant, priceId): Promise<string>` (returns Stripe url); `createPortalLink(tenant): Promise<string>`; `functionsBaseUrl()`; `useSubscription(tenant)` → `{ data: BillingSummary | undefined, isLoading, isError }`. Type `BillingSummary = { plan_tier, subscription_status, key_quota, keys_used, current_period_end, trial_ends_at }`.

- [ ] **Step 1: Write the failing test**

```ts
// apps/web/src/lib/api/billing.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import { getBilling, createCheckoutSession } from "./billing";

// billing.ts uses the shared apiFetch (BFF) for getBilling and a bare fetch to
// the functions endpoint for checkout/portal. Mock global fetch.
describe("billing api", () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it("getBilling calls the BFF billing route and returns the summary", async () => {
    const summary = { plan_tier: "growth", subscription_status: "active",
      key_quota: 25000, keys_used: 42, current_period_end: null, trial_ends_at: null };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(summary), { status: 200 }));
    const out = await getBilling("aeronta-demo");
    expect(out.keys_used).toBe(42);
    const url = (globalThis.fetch as any).mock.calls[0][0] as string;
    expect(url).toContain("/v1/tenants/aeronta-demo/billing");
  });

  it("createCheckoutSession posts price_id to the function and returns the url", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ url: "https://stripe/checkout" }), { status: 200 }));
    const url = await createCheckoutSession("aeronta-demo", "price_growth");
    expect(url).toBe("https://stripe/checkout");
    const call = (globalThis.fetch as any).mock.calls[0];
    expect(call[0]).toContain("/functions/v1/create-checkout-session");
    expect(JSON.parse(call[1].body).price_id).toBe("price_growth");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npm test -- billing.test.ts`
Expected: FAIL — `./billing` module absent.

- [ ] **Step 3: Implement**

```ts
// apps/web/src/lib/api/billing.ts
import { request } from "./client";        // existing BFF fetch helper: request<T>(path, init?)
import { supabase } from "../auth/supabase";

export type BillingSummary = {
  plan_tier: string;
  subscription_status:
    | "trialing" | "active" | "past_due" | "canceled"
    | "incomplete" | "incomplete_expired" | "unpaid" | "paused" | null;
  key_quota: number;
  keys_used: number;
  current_period_end: string | null;
  trial_ends_at: string | null;
};

export function functionsBaseUrl(): string {
  const base = (import.meta.env.VITE_SUPABASE_URL as string | undefined) ?? "";
  return `${base.replace(/\/$/, "")}/functions/v1`;
}

export function getBilling(tenant: string): Promise<BillingSummary> {
  return request<BillingSummary>(`/v1/tenants/${tenant}/billing`);
}

async function callFunction(name: string, body: unknown): Promise<{ url: string }> {
  const token = (await supabase?.auth.getSession())?.data.session?.access_token;
  const res = await fetch(`${functionsBaseUrl()}/${name}`, {
    method: "POST",
    headers: { "content-type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${name} failed: ${res.status}`);
  return res.json();
}

export async function createCheckoutSession(_tenant: string, priceId: string): Promise<string> {
  return (await callFunction("create-checkout-session", { price_id: priceId })).url;
}
export async function createPortalLink(_tenant: string): Promise<string> {
  return (await callFunction("create-portal-link", {})).url;
}
```

`request<T>(path, init?)` is the existing BFF fetch helper in `client.ts` (adds `BASE_URL` + the bearer set via `setAccessToken`). Do not invent a new fetch path.

```ts
// apps/web/src/lib/api/useSubscription.ts
import { useQuery } from "@tanstack/react-query";
import { getBilling, type BillingSummary } from "./billing";

export function useSubscription(tenant: string) {
  return useQuery<BillingSummary>({
    queryKey: ["billing", tenant],
    queryFn: () => getBilling(tenant),
    staleTime: 60_000,
  });
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npm test -- billing.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/api/billing.ts apps/web/src/lib/api/useSubscription.ts apps/web/src/lib/api/billing.test.ts
git commit -m "feat(web): billing api client + useSubscription + edge-function callers"
```

---

### Task 10: `/billing` plan & usage page + owner-gated nav

**Files:**
- Create: `apps/web/src/features/billing/BillingPage.tsx`, `.../BillingPage.test.tsx`
- Modify: `apps/web/src/App.tsx` (route `/billing`; nav item "Billing" owner-only)

**Interfaces:**
- Consumes: `useSubscription` (Task 9), `createPortalLink`/`createCheckoutSession` (Task 9), the tenant slug + role from `useAuth` (existing), `plan_tiers`/`prices` for the "Start subscription" tier list (fetch active prices via the anon supabase client, or reuse a small `getPublicPrices()` added to `billing.ts`).
- Produces: `<BillingPage/>` rendering four states from `subscription_status` — Provisioning / Active / read-only — with a usage meter (`keys_used`/`key_quota`), "Manage billing" (Portal) and "Start subscription" (Checkout) buttons.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/features/billing/BillingPage.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BillingPage } from "./BillingPage";
import * as billing from "../../lib/api/billing";

function renderWith(summary: Partial<billing.BillingSummary>) {
  vi.spyOn(billing, "getBilling").mockResolvedValue({
    plan_tier: "growth", subscription_status: "active", key_quota: 25000,
    keys_used: 5000, current_period_end: null, trial_ends_at: null, ...summary,
  } as billing.BillingSummary);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><BillingPage tenant="acme" role="owner" /></QueryClientProvider>);
}

describe("BillingPage", () => {
  it("active plan shows the tier, a usage meter, and Manage billing", async () => {
    renderWith({ subscription_status: "active", plan_tier: "growth" });
    expect(await screen.findByText(/growth/i)).toBeInTheDocument();
    expect(await screen.findByText(/5,?000 \/ 25,?000/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /manage billing/i })).toBeInTheDocument();
  });
  it("read-only (canceled) shows reactivate", async () => {
    renderWith({ subscription_status: "canceled" });
    expect(await screen.findByText(/read-only|reactivate/i)).toBeInTheDocument();
  });
  it("provisioning (null) shows Start subscription", async () => {
    renderWith({ subscription_status: null });
    expect(await screen.findByRole("button", { name: /start subscription/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npm test -- BillingPage.test.tsx`
Expected: FAIL — component absent.

- [ ] **Step 3: Implement `BillingPage.tsx`**

```tsx
// apps/web/src/features/billing/BillingPage.tsx
import { useSubscription } from "../../lib/api/useSubscription";
import { createPortalLink, createCheckoutSession } from "../../lib/api/billing";
import { Button } from "../../components/ui/button";
import { QueryState } from "../../components/QueryState"; // existing loading/error helper

const ACTIVE = new Set(["trialing", "active", "past_due"]);
const READONLY = new Set(["canceled", "unpaid", "paused"]);

export function BillingPage({ tenant, role }: { tenant: string; role: string }) {
  const q = useSubscription(tenant);
  return (
    <QueryState query={q}>
      {(s) => {
        const status = s.subscription_status;
        const state = status && ACTIVE.has(status) ? "active"
          : status && READONLY.has(status) ? "readonly" : "provisioning";
        const pct = Math.min(100, Math.round((s.keys_used / s.key_quota) * 100));
        return (
          <div className="space-y-6">
            <h1 className="text-xl font-semibold">Billing &amp; usage</h1>
            <div>
              <div className="text-sm text-muted-foreground">Plan</div>
              <div className="text-lg capitalize">{s.plan_tier}</div>
              {status && <div className="text-sm">Status: {status}</div>}
            </div>
            <div>
              <div className="text-sm text-muted-foreground">Managed part-location keys</div>
              <div>{s.keys_used.toLocaleString()} / {s.key_quota.toLocaleString()}</div>
              <div className="h-2 bg-muted rounded"><div
                className={`h-2 rounded ${pct >= 100 ? "bg-destructive" : "bg-primary"}`}
                style={{ width: `${pct}%` }} /></div>
              {pct >= 100 && <div className="text-sm text-destructive">
                Over quota — upgrade to ingest more keys.</div>}
            </div>
            {state === "readonly" && <div className="text-destructive">
              Your subscription lapsed — the workspace is read-only. Reactivate to resume writes.</div>}
            {role === "owner" ? (
              state === "provisioning"
                ? <StartButton tenant={tenant} />
                : <Button onClick={async () => { location.href = await createPortalLink(tenant); }}>
                    Manage billing</Button>
            ) : <div className="text-sm text-muted-foreground">
              Ask an owner to manage billing.</div>}
          </div>
        );
      }}
    </QueryState>
  );
}

function StartButton({ tenant }: { tenant: string }) {
  // Minimal: link to /signup?plan flow reuse, or fetch the default price. For the
  // provisioning re-entry we reuse the growth price id from public prices.
  return <Button onClick={async () => {
    const priceId = await defaultPriceId();
    location.href = await createCheckoutSession(tenant, priceId);
  }}>Start subscription</Button>;
}

async function defaultPriceId(): Promise<string> {
  // Reuse getPublicPrices (Task 9 add) — pick the first active monthly price.
  const { getPublicPrices } = await import("../../lib/api/billing");
  const prices = await getPublicPrices();
  const monthly = prices.find((p) => p.interval === "month") ?? prices[0];
  return monthly.id;
}
```

Add `getPublicPrices()` to `billing.ts` (reads active prices via the anon supabase client; falls back to `[]` when `supabase` is null):

```ts
// billing.ts — append
export type PublicPrice = { id: string; product_id: string; unit_amount: number | null;
  currency: string | null; interval: string | null; tier: string | null };
export async function getPublicPrices(): Promise<PublicPrice[]> {
  if (!supabase) return [];
  const { data } = await supabase.from("prices")
    .select("id,product_id,unit_amount,currency,interval,metadata").eq("active", true);
  return (data ?? []).map((p: any) => ({ ...p, tier: p.metadata?.tier ?? null }));
}
```

In `App.tsx`: add `<Route path="/billing" element={<BillingPage tenant={activeSlug} role={role} />} />` and a nav entry gated on `role === "owner"` (mirror how "Members" is gated to admin+).

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npm test -- BillingPage.test.tsx`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/billing/BillingPage.tsx apps/web/src/features/billing/BillingPage.test.tsx apps/web/src/lib/api/billing.ts apps/web/src/App.tsx
git commit -m "feat(web): /billing plan & usage page + owner-gated nav"
```

---

### Task 11: `/signup` onboarding wizard

**Files:**
- Create: `apps/web/src/features/billing/SignupWizard.tsx`, `.../SignupWizard.test.tsx`
- Modify: `apps/web/src/App.tsx` (public route `/signup`, reachable pre-auth)

**Interfaces:**
- Consumes: `supabase.auth.signUp` / `getSession` / `refreshSession` (Supabase), `create_tenant_for_current_user` RPC (Task 3, via `supabase.rpc`), `createCheckoutSession` (Task 9), `?plan=<tier>` query param, `getPublicPrices` (Task 10).
- Produces: `<SignupWizard/>` — steps: (1) create account (email/password) → (2) "confirm your email" interstitial → (3) name org (calls the RPC, refreshes session) → (4) pick monthly/annual for the `?plan` tier → redirect to Checkout. Handles already-logged-in users (skip step 1).

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/features/billing/SignupWizard.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SignupWizard } from "./SignupWizard";

// Mock the supabase client + billing api so the wizard runs without network.
vi.mock("../../lib/auth/supabase", () => ({
  supabase: {
    auth: {
      signUp: vi.fn().mockResolvedValue({ data: { session: null }, error: null }),
      getSession: vi.fn().mockResolvedValue({ data: { session: { user: { id: "u1" } } } }),
      refreshSession: vi.fn().mockResolvedValue({ data: { session: {} }, error: null }),
    },
    rpc: vi.fn().mockResolvedValue({ data: "tenant-uuid", error: null }),
  },
  authEnabled: true,
}));
vi.mock("../../lib/api/billing", () => ({
  createCheckoutSession: vi.fn().mockResolvedValue("https://stripe/checkout"),
  getPublicPrices: vi.fn().mockResolvedValue([
    { id: "price_g_m", tier: "growth", interval: "month", unit_amount: 29900, currency: "usd" },
  ]),
}));

describe("SignupWizard", () => {
  it("new signup requires email confirmation before org step", async () => {
    render(<SignupWizard initialPlan="growth" />);
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "a@b.co" } });
    fireEvent.change(screen.getByLabelText(/password/i), { target: { value: "pw12345678" } });
    fireEvent.click(screen.getByRole("button", { name: /create account/i }));
    // signUp returned no session (confirmation required) → interstitial shown.
    expect(await screen.findByText(/confirm your email/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npm test -- SignupWizard.test.tsx`
Expected: FAIL — component absent.

- [ ] **Step 3: Implement `SignupWizard.tsx`**

```tsx
// apps/web/src/features/billing/SignupWizard.tsx
import { useState } from "react";
import { supabase } from "../../lib/auth/supabase";
import { createCheckoutSession, getPublicPrices } from "../../lib/api/billing";
import { Button } from "../../components/ui/button";

type Step = "account" | "confirm" | "org" | "plan";

export function SignupWizard({ initialPlan }: { initialPlan: string }) {
  const [step, setStep] = useState<Step>("account");
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [orgName, setOrgName] = useState(""); const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function createAccount() {
    setBusy(true); setErr(null);
    const { data, error } = await supabase!.auth.signUp({ email, password });
    setBusy(false);
    if (error) { setErr(error.message); return; }
    // Confirmation required: signUp returns no session until the email is confirmed.
    setStep(data.session ? "org" : "confirm");
  }

  async function continueAfterConfirm() {
    // User clicked the email link and came back; a session should now exist.
    const { data } = await supabase!.auth.getSession();
    if (data.session) setStep("org"); else setErr("Please confirm your email, then retry.");
  }

  async function createOrg() {
    setBusy(true); setErr(null);
    const { data, error } = await supabase!.rpc("create_tenant_for_current_user", { p_name: orgName });
    if (error) { setBusy(false); setErr(error.message); return; }
    await supabase!.auth.refreshSession();  // pick up the new tenant_id claim
    setBusy(false); setStep("plan");
  }

  async function goToCheckout(interval: "month" | "year") {
    setBusy(true);
    const prices = await getPublicPrices();
    const price = prices.find((p) => p.tier === initialPlan && p.interval === interval)
      ?? prices.find((p) => p.tier === initialPlan);
    if (!price) { setErr("No price configured for this plan."); setBusy(false); return; }
    const session = await supabase!.auth.getSession();
    const tenant = session.data.session?.user ? "me" : "me"; // slug resolved via claim in the fn
    location.href = await createCheckoutSession(tenant, price.id);
  }

  return (
    <div className="max-w-md mx-auto space-y-4 p-6">
      {err && <div className="text-destructive text-sm">{err}</div>}
      {step === "account" && (<>
        <h1 className="text-xl font-semibold">Start your 14-day free trial</h1>
        <label className="block text-sm">Email
          <input aria-label="email" className="w-full border rounded p-2" value={email}
            onChange={(e) => setEmail(e.target.value)} /></label>
        <label className="block text-sm">Password
          <input aria-label="password" type="password" className="w-full border rounded p-2"
            value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        <Button disabled={busy} onClick={createAccount}>Create account</Button>
      </>)}
      {step === "confirm" && (<>
        <h1 className="text-xl font-semibold">Confirm your email</h1>
        <p className="text-sm">We sent a link to {email}. Confirm it, then continue.</p>
        <Button disabled={busy} onClick={continueAfterConfirm}>I've confirmed — continue</Button>
      </>)}
      {step === "org" && (<>
        <h1 className="text-xl font-semibold">Name your organization</h1>
        <input aria-label="organization" className="w-full border rounded p-2" value={orgName}
          onChange={(e) => setOrgName(e.target.value)} />
        <Button disabled={busy || orgName.length < 2} onClick={createOrg}>Continue</Button>
      </>)}
      {step === "plan" && (<>
        <h1 className="text-xl font-semibold capitalize">{initialPlan} plan · 14-day trial</h1>
        <div className="flex gap-3">
          <Button disabled={busy} onClick={() => goToCheckout("month")}>Monthly</Button>
          <Button disabled={busy} variant="outline" onClick={() => goToCheckout("year")}>Annual</Button>
        </div>
      </>)}
    </div>
  );
}
```

In `App.tsx`: register `/signup` as a **public** route (outside the authed shell) that reads `?plan` (default `growth`) and renders `<SignupWizard initialPlan={plan} />`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npm test -- SignupWizard.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/billing/SignupWizard.tsx apps/web/src/features/billing/SignupWizard.test.tsx apps/web/src/App.tsx
git commit -m "feat(web): /signup onboarding wizard (account -> org -> checkout)"
```

---

### Task 12: Subscription banners + over-quota upgrade CTA

**Files:**
- Create: `apps/web/src/features/billing/SubscriptionBanner.tsx`, `.../SubscriptionBanner.test.tsx`
- Modify: `apps/web/src/App.tsx` (mount `<SubscriptionBanner/>` in the app shell)
- Modify: `apps/web/src/features/feeds/UploadPanel.tsx` (over-quota ingest error → "Upgrade" link to `/billing`)

**Interfaces:**
- Consumes: `useSubscription` (Task 9), the active tenant slug (existing app context).
- Produces: `<SubscriptionBanner tenant=.../>` — renders nothing when `active`; a trial-countdown banner when `trialing`; an "update card" banner when `past_due`; a "reactivate" banner when read-only; each links to `/billing`.

- [ ] **Step 1: Write the failing test**

```tsx
// apps/web/src/features/billing/SubscriptionBanner.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SubscriptionBanner } from "./SubscriptionBanner";
import * as billing from "../../lib/api/billing";

function renderWith(status: billing.BillingSummary["subscription_status"], extra = {}) {
  vi.spyOn(billing, "getBilling").mockResolvedValue({
    plan_tier: "growth", subscription_status: status, key_quota: 25000, keys_used: 1,
    current_period_end: null, trial_ends_at: "2099-01-01T00:00:00Z", ...extra,
  } as billing.BillingSummary);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><SubscriptionBanner tenant="acme" /></QueryClientProvider>);
}

describe("SubscriptionBanner", () => {
  it("active renders nothing", async () => {
    const { container } = renderWith("active");
    await Promise.resolve();
    expect(container.textContent).toBe("");
  });
  it("past_due prompts to update card", async () => {
    renderWith("past_due");
    expect(await screen.findByText(/update.*card|payment/i)).toBeInTheDocument();
  });
  it("canceled prompts to reactivate", async () => {
    renderWith("canceled");
    expect(await screen.findByText(/reactivate|read-only/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npm test -- SubscriptionBanner.test.tsx`
Expected: FAIL — component absent.

- [ ] **Step 3: Implement**

```tsx
// apps/web/src/features/billing/SubscriptionBanner.tsx
import { useSubscription } from "../../lib/api/useSubscription";

const READONLY = new Set(["canceled", "unpaid", "paused"]);

export function SubscriptionBanner({ tenant }: { tenant: string }) {
  const { data } = useSubscription(tenant);
  if (!data) return null;
  const s = data.subscription_status;
  let msg: string | null = null;
  if (s === "trialing" && data.trial_ends_at)
    msg = `Free trial — ends ${new Date(data.trial_ends_at).toLocaleDateString()}.`;
  else if (s === "past_due") msg = "Payment failed — update your card to avoid interruption.";
  else if (s && READONLY.has(s)) msg = "Subscription lapsed — workspace is read-only. Reactivate to resume.";
  else if (!s || s === "incomplete" || s === "incomplete_expired")
    msg = "Finish subscribing to start using Aeronta.";
  if (!msg) return null;
  const tone = s === "trialing" ? "bg-primary/10" : "bg-destructive/10 text-destructive";
  return (
    <div className={`px-4 py-2 text-sm flex justify-between items-center ${tone}`}>
      <span>{msg}</span>
      <a href="#/billing" className="underline">Manage billing</a>
    </div>
  );
}
```

Mount `<SubscriptionBanner tenant={activeSlug} />` at the top of the authed app shell in `App.tsx` (only when `authEnabled` and a tenant is active). In `UploadPanel.tsx`, where the ingest job surfaces an over-quota error (the C3 error string mentions quota), append a link: `<a href="#/billing">Upgrade your plan</a>`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npm test -- SubscriptionBanner.test.tsx`
Expected: PASS. Then the full web suite (regression): `npm test`.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/billing/SubscriptionBanner.tsx apps/web/src/features/billing/SubscriptionBanner.test.tsx apps/web/src/App.tsx apps/web/src/features/feeds/UploadPanel.tsx
git commit -m "feat(web): subscription banners + over-quota upgrade CTA"
```

---

## GROUP 5 — apps/site (Astro marketing/docs)

> New Astro project at `apps/site`, deployed to its **own** Vercel project (CLI from `apps/site`, or Root Directory = `apps/site` — never root-built). Tests here are `npm run build` (a broken page fails the build) plus one island unit test.

### Task 13: Scaffold `apps/site` + shared Tailwind preset + Home

**Files:**
- Create: `packages/tailwind-preset/index.js`, `packages/tailwind-preset/package.json`
- Modify: `apps/web/tailwind.config.js` (consume the preset — no visual change)
- Create: `apps/site/{package.json,astro.config.mjs,tailwind.config.mjs,tsconfig.json}`
- Create: `apps/site/src/layouts/Base.astro`, `apps/site/src/pages/index.astro`, `apps/site/public/robots.txt`

**Interfaces:**
- Produces: a buildable Astro site with a shared Tailwind theme; `<Base>` layout (nav + footer + meta/OG); home page with hero + the three differentiators + "Start free trial" → `${PUBLIC_APP_URL}/#/signup?plan=growth`.
- Consumes: the Airvoyant theme tokens currently inline in `apps/web/tailwind.config.js` (extracted to the preset).

- [ ] **Step 1: Extract the shared preset**

Move the `theme.extend` (colors/fonts/radii — the Airvoyant tokens) from `apps/web/tailwind.config.js` into `packages/tailwind-preset/index.js` as `module.exports = { theme: { extend: { /* tokens */ } } }`, and change `apps/web/tailwind.config.js` to `presets: [require("../../packages/tailwind-preset")]` keeping its `content` globs. Add `packages/tailwind-preset/package.json` (`{"name":"@aeronta/tailwind-preset","version":"0.0.0","main":"index.js"}`).

- [ ] **Step 2: Verify apps/web still builds unchanged**

Run: `cd apps/web && npm run build`
Expected: PASS — build succeeds, no visual token lost (the preset is the same tokens).

- [ ] **Step 3: Scaffold Astro**

```jsonc
// apps/site/package.json
{
  "name": "aeronta-site", "type": "module", "private": true,
  "scripts": { "dev": "astro dev", "build": "astro build", "preview": "astro preview" },
  "dependencies": {
    "astro": "^4.15.0", "@astrojs/react": "^3.6.0", "@astrojs/tailwind": "^5.1.0",
    "@astrojs/mdx": "^3.1.0", "@astrojs/sitemap": "^3.1.0",
    "react": "^18.3.1", "react-dom": "^18.3.1",
    "tailwindcss": "^3.4.0", "@supabase/supabase-js": "^2.45.0"
  }
}
```
```js
// apps/site/astro.config.mjs
import { defineConfig } from "astro/config";
import react from "@astrojs/react";
import tailwind from "@astrojs/tailwind";
import mdx from "@astrojs/mdx";
import sitemap from "@astrojs/sitemap";
export default defineConfig({
  site: process.env.PUBLIC_SITE_URL ?? "https://aeronta.example",
  integrations: [react(), tailwind(), mdx(), sitemap()],
});
```
```js
// apps/site/tailwind.config.mjs
export default {
  presets: [require("../../packages/tailwind-preset")],
  content: ["./src/**/*.{astro,tsx,mdx}"],
};
```
```astro
---
// apps/site/src/layouts/Base.astro
const { title = "Aeronta Inventory", description = "AI inventory optimization for airline spares." } = Astro.props;
const appUrl = import.meta.env.PUBLIC_APP_URL ?? "https://aeronta-inventory.vercel.app";
---
<!doctype html><html lang="en"><head>
  <meta charset="utf-8" /><meta name="viewport" content="width=device-width" />
  <title>{title}</title><meta name="description" content={description} />
  <meta property="og:title" content={title} /><meta property="og:description" content={description} />
</head><body class="min-h-screen bg-background text-foreground">
  <header class="flex justify-between items-center px-6 py-4 border-b">
    <a href="/" class="font-semibold">Aeronta Inventory</a>
    <nav class="flex gap-4 text-sm">
      <a href="/product">Product</a><a href="/pricing">Pricing</a>
      <a href="/docs">Docs</a><a href="/security">Security</a><a href="/contact">Contact</a>
      <a href={`${appUrl}/#/signup?plan=growth`} class="font-medium">Start free trial</a>
    </nav>
  </header>
  <main class="max-w-5xl mx-auto px-6 py-12"><slot /></main>
  <footer class="px-6 py-8 border-t text-sm text-muted-foreground">© Aeronta Inventory</footer>
</body></html>
```
```astro
---
// apps/site/src/pages/index.astro
import Base from "../layouts/Base.astro";
const appUrl = import.meta.env.PUBLIC_APP_URL ?? "https://aeronta-inventory.vercel.app";
---
<Base>
  <section class="text-center space-y-4">
    <h1 class="text-4xl font-bold">Governed autonomy for airline spares inventory</h1>
    <p class="text-lg text-muted-foreground">Recommend, govern, and act on ROP/EOQ/safety-stock — with a full audit trail.</p>
    <a href={`${appUrl}/#/signup?plan=growth`} class="inline-block px-5 py-3 rounded bg-primary text-primary-foreground">Start free trial</a>
  </section>
  <section class="grid md:grid-cols-3 gap-6 mt-16">
    <div><h3 class="font-semibold">Native eMRO depth</h3><p class="text-sm">The connector already exists.</p></div>
    <div><h3 class="font-semibold">Governed write-back</h3><p class="text-sm">Tiers, guardrails, audit ledger — we act, not just recommend.</p></div>
    <div><h3 class="font-semibold">BVR savings attribution</h3><p class="text-sm">Every change traced to dollars.</p></div>
  </section>
</Base>
```
```
# apps/site/public/robots.txt
User-agent: *
Allow: /
Sitemap: /sitemap-index.xml
```

- [ ] **Step 4: Build**

Run: `cd apps/site && npm install && npm run build`
Expected: PASS — `dist/` produced, `index.html` + `sitemap-index.xml` present.

- [ ] **Step 5: Commit**

```bash
git add packages/tailwind-preset apps/web/tailwind.config.js apps/site
git commit -m "feat(site): scaffold Astro apps/site + shared tailwind preset + home"
```

---

### Task 14: Pricing (build-time fetch) + Product + Security pages

**Files:**
- Create: `apps/site/src/lib/supabase.ts`, `apps/site/src/pages/pricing.astro`, `apps/site/src/pages/product.astro`, `apps/site/src/pages/security.astro`

**Interfaces:**
- Consumes: public `plan_tiers` + active `prices` (anon key, public-read RLS from Tasks 1–2) via `PUBLIC_SUPABASE_URL`/`PUBLIC_SUPABASE_ANON_KEY` at build time.
- Produces: a crawlable pricing page rendering the three self-serve tiers with their price + quota, each "Start free trial" → `${PUBLIC_APP_URL}/#/signup?plan=<tier>`.

- [ ] **Step 1: Implement the build-time data helper + pricing page**

```ts
// apps/site/src/lib/supabase.ts
import { createClient } from "@supabase/supabase-js";
const url = import.meta.env.PUBLIC_SUPABASE_URL;
const anon = import.meta.env.PUBLIC_SUPABASE_ANON_KEY;
export const supabase = url && anon ? createClient(url, anon) : null;

export type Tier = { tier: string; display_name: string; key_quota: number;
  unit_amount: number | null; currency: string | null; interval: string | null };

export async function getPricingTiers(): Promise<Tier[]> {
  if (!supabase) return [];  // build without env → empty; page still renders
  const { data: tiers } = await supabase.from("plan_tiers")
    .select("tier,display_name,key_quota,sort").order("sort");
  const { data: prices } = await supabase.from("prices")
    .select("unit_amount,currency,interval,metadata").eq("active", true);
  return (tiers ?? []).map((t: any) => {
    const p = (prices ?? []).find((x: any) => x.metadata?.tier === t.tier && x.interval === "month");
    return { ...t, unit_amount: p?.unit_amount ?? null, currency: p?.currency ?? null,
      interval: p?.interval ?? "month" };
  });
}
```
```astro
---
// apps/site/src/pages/pricing.astro
import Base from "../layouts/Base.astro";
import { getPricingTiers } from "../lib/supabase";
const tiers = await getPricingTiers();
const appUrl = import.meta.env.PUBLIC_APP_URL ?? "https://aeronta-inventory.vercel.app";
const fmt = (a: number | null, c: string | null) =>
  a == null ? "Contact us" : `${c === "usd" ? "$" : ""}${(a / 100).toLocaleString()}/mo`;
---
<Base title="Pricing — Aeronta Inventory">
  <h1 class="text-3xl font-bold mb-8">Pricing</h1>
  <div class="grid md:grid-cols-3 gap-6">
    {tiers.map((t) => (
      <div class="border rounded p-6 space-y-3">
        <h3 class="font-semibold text-lg">{t.display_name}</h3>
        <div class="text-2xl">{fmt(t.unit_amount, t.currency)}</div>
        <div class="text-sm text-muted-foreground">Up to {t.key_quota.toLocaleString()} part-location keys</div>
        <a href={`${appUrl}/#/signup?plan=${t.tier}`} class="inline-block px-4 py-2 rounded bg-primary text-primary-foreground">Start free trial</a>
      </div>
    ))}
    <div class="border rounded p-6 space-y-3">
      <h3 class="font-semibold text-lg">Enterprise</h3><div class="text-2xl">Contact us</div>
      <div class="text-sm text-muted-foreground">SSO, connectors, custom terms</div>
      <a href="/contact" class="inline-block px-4 py-2 rounded border">Book a demo</a>
    </div>
  </div>
</Base>
```

Add `product.astro` and `security.astro` as static `<Base>` content pages (product = the recommend→govern→act loop; security = RLS isolation, SOC 2 posture, encryption, audit ledger — prose, no data fetch).

- [ ] **Step 2: Build with and without env**

Run: `cd apps/site && npm run build`
Expected: PASS even with no `PUBLIC_SUPABASE_*` (pricing renders Enterprise + empty self-serve grid). With env set, tiers render.

- [ ] **Step 3: Commit**

```bash
git add apps/site/src/lib/supabase.ts apps/site/src/pages/pricing.astro apps/site/src/pages/product.astro apps/site/src/pages/security.astro
git commit -m "feat(site): pricing (build-time fetch) + product + security pages"
```

---

### Task 15: Docs (connector spec) + Contact/Book-a-demo (leads)

**Files:**
- Create: `apps/site/src/pages/docs.mdx`, `apps/site/src/pages/contact.astro`, `apps/site/src/components/ContactForm.tsx`, `apps/site/src/components/ContactForm.test.tsx`

**Interfaces:**
- Consumes: the C3 canonical contract (`services/recommendation-engine/src/trax_io_reco/ingest/canonical.py` — mirror it in prose), the `leads` insert path (Task 3) via the anon supabase client.
- Produces: a docs page stating the 6-file/column upload contract *is* the validator/connector spec; a contact form (React island) that inserts into `leads` with a honeypot.

- [ ] **Step 1: Write the failing island test**

```tsx
// apps/site/src/components/ContactForm.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ContactForm } from "./ContactForm";

const insert = vi.fn().mockResolvedValue({ error: null });
vi.mock("../lib/supabase", () => ({ supabase: { from: () => ({ insert }) } }));

describe("ContactForm", () => {
  it("submits name/email/message to leads and shows a thank-you", async () => {
    render(<ContactForm />);
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "a@b.co" } });
    fireEvent.change(screen.getByLabelText(/message/i), { target: { value: "demo please" } });
    fireEvent.click(screen.getByRole("button", { name: /send|book/i }));
    await waitFor(() => expect(insert).toHaveBeenCalled());
    expect(insert.mock.calls[0][0]).toMatchObject({ email: "a@b.co", source: "contact" });
    expect(await screen.findByText(/thank/i)).toBeInTheDocument();
  });

  it("does not submit when the honeypot is filled (bot)", async () => {
    insert.mockClear();
    render(<ContactForm />);
    // The honeypot input is visually hidden; a bot fills it.
    fireEvent.change(screen.getByLabelText(/company website/i), { target: { value: "spam" } });
    fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "a@b.co" } });
    fireEvent.change(screen.getByLabelText(/message/i), { target: { value: "x" } });
    fireEvent.click(screen.getByRole("button", { name: /send|book/i }));
    await new Promise((r) => setTimeout(r, 10));
    expect(insert).not.toHaveBeenCalled();
  });
});
```

(Add a minimal Vitest config to `apps/site` — `vitest` + `@testing-library/react` + `jsdom` — mirroring `apps/web`'s test setup, if not already present.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/site && npx vitest run ContactForm.test.tsx`
Expected: FAIL — component absent.

- [ ] **Step 3: Implement `ContactForm.tsx` + pages**

```tsx
// apps/site/src/components/ContactForm.tsx
import { useState } from "react";
import { supabase } from "../lib/supabase";

export function ContactForm() {
  const [sent, setSent] = useState(false);
  const [hp, setHp] = useState("");            // honeypot
  const [form, setForm] = useState({ name: "", email: "", company: "", message: "" });
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (hp) return;                            // bot filled the honeypot → drop silently
    await supabase?.from("leads").insert({ ...form, source: "contact" });
    setSent(true);
  }
  if (sent) return <p>Thank you — we'll be in touch shortly.</p>;
  return (
    <form onSubmit={submit} class="space-y-3 max-w-md">
      <label class="sr-only" aria-hidden="true">Company website
        <input aria-label="company website" tabIndex={-1} autoComplete="off"
          className="hidden" value={hp} onChange={(e) => setHp(e.target.value)} /></label>
      <input aria-label="name" placeholder="Name" className="w-full border rounded p-2"
        value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
      <input aria-label="email" placeholder="Email" required className="w-full border rounded p-2"
        value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
      <textarea aria-label="message" placeholder="How can we help?" className="w-full border rounded p-2"
        value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} />
      <button type="submit" className="px-4 py-2 rounded bg-primary text-primary-foreground">Book a demo</button>
    </form>
  );
}
```
```astro
---
// apps/site/src/pages/contact.astro
import Base from "../layouts/Base.astro";
import { ContactForm } from "../components/ContactForm";
---
<Base title="Contact — Aeronta Inventory">
  <h1 class="text-3xl font-bold mb-6">Book a demo</h1>
  <ContactForm client:load />
</Base>
```

`docs.mdx`: a `<Base>`-wrapped MDX page documenting the 6 canonical files (parts, stock, demand_history, locations, open_orders, vendors), their columns (mirroring `canonical.py`), which are required (parts + stock), and a sentence: "This table is the validator contract — a file that does not match is rejected at ingest." Keep the column lists in sync with `canonical.py`.

- [ ] **Step 4: Run to verify it passes + build**

Run: `cd apps/site && npx vitest run ContactForm.test.tsx && npm run build`
Expected: PASS + build succeeds (docs + contact pages emit).

- [ ] **Step 5: Commit**

```bash
git add apps/site/src/pages/docs.mdx apps/site/src/pages/contact.astro apps/site/src/components/ContactForm.tsx apps/site/src/components/ContactForm.test.tsx apps/site/vitest.config.ts
git commit -m "feat(site): docs (connector spec) + contact/book-a-demo leads form"
```

---

## GROUP 6 — Rollout + bookkeeping

### Task 16: Playwright e2e + live billing smoke stage + rollout runbook

**Files:**
- Create: `apps/web/e2e/signup-billing.spec.ts` (route-mocked, best-effort — mirrors the existing `workbench-accept.spec.ts` posture)
- Modify: `deploy/aeronta_smoke.py` (env-gated `AERONTA_SMOKE_BILLING=1` stage)
- Create: `deploy/C4_ROLLOUT.md`

**The Playwright e2e (spec §7 headline gate):** the repo's e2e is route-mocked and best-effort (`npm run e2e`, no backend). Add `apps/web/e2e/signup-billing.spec.ts` that route-mocks the BFF `GET /v1/tenants/*/billing` (and the edge-function calls) to drive the frontend billing states: load `#/billing` with a `provisioning` summary → "Start subscription" visible; with a `canceled` summary → read-only/reactivate visible; with an `active` summary → "Manage billing" visible + usage meter. The FULL signup→Stripe-test-checkout→upload→recommendation→approve chain needs the deployed functions + Stripe test mode and is verified via the rollout runbook below (not automatable in the route-mocked e2e). Run: `cd apps/web && npm run e2e -- signup-billing`. Expected: PASS (best-effort).

**Interfaces:**
- Consumes: the deployed Edge Functions + webhook, live Stripe **test mode**, the C2 smoke's sign-in helper.
- Produces: a smoke stage that (in Stripe test mode) asserts the checkout→webhook→`tenants.plan_tier` path applied; a runbook for the controller's live steps.

> Creating Stripe products/prices, setting secrets, registering the webhook, and `supabase functions deploy` are **controller steps** (like C3 Task 7's live run) — this task ships the *automatable* smoke + the runbook; the controller executes the live wiring.

- [ ] **Step 1: Add the smoke stage**

Add to `deploy/aeronta_smoke.py` (mirroring the existing `AERONTA_SMOKE_INGEST` gate): when `AERONTA_SMOKE_BILLING=1` and `AERONTA_BFF_URL` set, sign in as the smoke owner, `GET /v1/tenants/{t}/billing`, assert a 200 with a `plan_tier` field and an integer `keys_used`. (Full checkout automation requires Stripe test fixtures; the smoke asserts the read path + that the gate/endpoint are live. A comment documents the manual test-checkout verification.)

```python
# deploy/aeronta_smoke.py — new stage (called from main when AERONTA_SMOKE_BILLING=1)
def _run_billing_stage(base: str, auth_headers: dict) -> None:
    r = httpx.get(f"{base}/billing", headers=auth_headers, timeout=15.0)
    if r.status_code != 200:
        _fail(f"billing: GET billing returned {r.status_code}: {r.text[:200]}")
    body = r.json()
    if "plan_tier" not in body or not isinstance(body.get("keys_used"), int):
        _fail(f"billing: unexpected summary shape: {body}")
    print(f"billing OK · plan={body['plan_tier']} status={body.get('subscription_status')} "
          f"keys_used={body['keys_used']}/{body.get('key_quota')}")
```

- [ ] **Step 2: Write the rollout runbook**

`deploy/C4_ROLLOUT.md` documents, in order: (1) create Stripe Products + Prices (test then live) with `price.metadata.tier ∈ {starter,growth,scale}`, monthly + annual; (2) `supabase secrets set STRIPE_SECRET_KEY=… STRIPE_WEBHOOK_SIGNING_SECRET=… APP_ORIGIN=…`; (3) `supabase functions deploy create-checkout-session create-portal-link` and `supabase functions deploy stripe-webhook --no-verify-jwt`; (4) register the Stripe webhook → `https://<ref>.supabase.co/functions/v1/stripe-webhook` for `product.*`, `price.*`, `customer.subscription.*`, `checkout.session.completed`; (5) `stripe listen --forward-to` for local dev; (6) deploy `apps/site` to its own Vercel project (CLI from `apps/site`, env `PUBLIC_SUPABASE_URL`/`PUBLIC_SUPABASE_ANON_KEY`/`PUBLIC_APP_URL`/`PUBLIC_SITE_URL`); (7) set `apps/web` env `VITE_SUPABASE_URL` (already set); (8) run `AERONTA_SMOKE_BILLING=1 aeronta_smoke.py`.

- [ ] **Step 3: Run the smoke locally against a stubbed status (dry check)**

Run: `python -c "import ast; ast.parse(open('deploy/aeronta_smoke.py').read())"` (syntax) and the existing smoke with billing gate unset (must still `SKIP`/pass).
Expected: no syntax error; existing stages unaffected.

- [ ] **Step 4: Commit**

```bash
git add deploy/aeronta_smoke.py deploy/C4_ROLLOUT.md
git commit -m "ops(billing): env-gated live billing smoke stage + C4 rollout runbook"
```

---

### Task 17: Bookkeeping

**Files:**
- Modify: `ROADMAP.md` (mark C4 done/live), `TASKS.md` (C4 section), `CLAUDE.md` (C4 surface + edge-functions note), the C4 spec status line.

- [ ] **Step 1:** In `ROADMAP.md`, mark the **C4 — Billing + Marketing Site** row `[x]` with today's date + a one-paragraph summary (Stripe Edge Functions, Astro site, self-serve signup, plan-driven quota + 402 gate); note the "Commercial track exit" milestone (self-serve signup → upload → recommendation → approve, billing active) is met.
- [ ] **Step 2:** In `TASKS.md`, add a "C4 billing + marketing shipped" section (code summary, test counts, live facts, carry-forwards).
- [ ] **Step 3:** In `CLAUDE.md` Section A, add a **C4** paragraph (the billing surfaces: Edge Functions in `supabase/functions/`, the 402 write-gate + `/billing` BFF read, `apps/web` signup/billing, the `apps/site` Astro marketing app + its deploy note) and add `apps/site` + `supabase/functions` test commands to the run/test table.
- [ ] **Step 4:** Update the C4 spec status line → `✅ Shipped <date>`.
- [ ] **Step 5: Commit**

```bash
git add ROADMAP.md TASKS.md CLAUDE.md docs/superpowers/specs/2026-07-23-c4-billing-marketing-design.md
git commit -m "docs: C4 bookkeeping — ROADMAP/TASKS/CLAUDE.md/spec status"
```

---

## Task dependency & sequencing

- **1 → 2 → 3** (migrations) are sequential (later tables reference earlier).
- **4 → 5 → 6** (edge functions) — 4 first (shared clients); 6 (webhook) needs the mirror (1–2).
- **7, 8** (BFF) depend on 1 (tenants columns) only; independent of edge functions.
- **9 → 10, 11, 12** (apps/web) depend on 8 (billing read) + 4–5 (function callers).
- **13 → 14 → 15** (apps/site) depend on 1–2 (public prices) for pricing; otherwise independent.
- **16, 17** last.

Groups 1–3 are the billing spine and should land first; 4–5 (frontend + site) can proceed once their backend deps are in; 6 is rollout.

