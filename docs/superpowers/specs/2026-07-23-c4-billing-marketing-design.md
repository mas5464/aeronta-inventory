# C4 — Billing + Marketing Site: Stripe tiers/webhooks/portal · self-serve signup · Astro sales site

**Date:** 2026-07-23
**Status:** ✅ Code complete 2026-07-23 (17 tasks, subagent-driven, per-task adversarial review; all suites green). **Live rollout pending** — Stripe account/products, secrets, `db push` 0010–0012, function + site deploys per [deploy/C4_ROLLOUT.md](../../../deploy/C4_ROLLOUT.md). Plan: [../plans/2026-07-23-c4-billing-marketing.md](../plans/2026-07-23-c4-billing-marketing.md).
**Owner:** Miguel Sosa
**Parent spec:** [2026-07-20-commercialization-architecture-design.md](2026-07-20-commercialization-architecture-design.md) (§7 Billing, §8 Marketing/signup, §10 row C4)
**Grounding:** [docs/research/2026-07-20-trax-io-saas-commercialization-research.md](../../research/2026-07-20-trax-io-saas-commercialization-research.md) (Stripe webhook-mirror pattern, Supabase Auth/SSO, pricing structure)
**Builds on the live stack:** Aeronta Inventory in production — `apps/web` (Vite/React) on Vercel (https://aeronta-inventory.vercel.app), FastAPI BFF + jobs worker on Railway (`aeronta`), Supabase `aeronta-inventory` (migrations 0001–0009), C3 self-serve ingest live end-to-end. C1 already ships `tenants.plan_tier` + `tenants.key_quota`, and C3's ingest already enforces `key_quota` — C4 makes that quota **plan-driven** and adds the money.

---

## Decisions locked

Three forks were decided during the brainstorm; the rest carry from the parent spec.

| Decision | Choice | Notes |
|---|---|---|
| **Scope** | **One combined C4** | billing backbone + signup funnel + full marketing site as a single sub-project, decomposed into six task-groups (§8) |
| **Billing runtime** | **Supabase Edge Functions (Deno)** | Stripe webhook + checkout/portal live next to the DB, off the FastAPI BFF; the `nextjs-subscription-payments` repo is a schema/logic *reference*, not the runtime |
| **Marketing stack** | **Astro** (`apps/site`, new) | content-first, best SEO, reuses the Tailwind/shadcn design via React islands; billing is no longer in Next.js, so Next.js loses its API-route advantage here |
| Plans (from parent §7) | 3 self-serve tiers banded by managed part-location keys — Starter ≤5K, Growth ≤25K, Scale ≤100K — + Enterprise (contact-us). Monthly + annual | exact $ deferred to launch; the system is **price-agnostic** (see §2) |
| Trial (from parent §7) | 14-day free trial on any tier, **card required** | |
| Lapse behavior (from parent §7) | lapsed subscription → **read-only, never lockout** | planners keep read access to their own history (§4) |
| Billing role (from parent §4) | billing = **owner** | checkout/portal owner-gated |

**Price-agnostic principle:** no dollar amount lives in code. Prices live in Stripe, are mirrored into Supabase, and render on the pricing page from the mirror. Finalizing price points = editing Stripe, no code change and no redeploy of the app (the marketing pricing page rebuilds — a rare event).

---

## 1. Architecture & component boundaries

C4 adds five well-bounded units around the existing stack. The existing planner API is unchanged in shape.

```
  prospect ─► apps/site (Astro, Vercel)          [NEW]  marketing + docs + pricing
                 │  "Start free trial" ─────────► app.<brand>/signup?plan=<tier>
                 ▼
  apps/web (Vite/React)                           [EXTENDED]
     • /signup onboarding wizard (account → org → checkout redirect)
     • /billing plan & usage page → Stripe Portal
     • global subscription banners (trial / past_due / read-only)
     └── calls Supabase Edge Functions directly (user JWT) for Stripe actions
                 │
                 ▼
  supabase/functions/  (Deno)                     [NEW]
     • create-checkout-session   (auth'd: mints Stripe Checkout for a tenant)
     • create-portal-link        (auth'd: opens the Customer Portal)
     • stripe-webhook            (public, signature-verified: mirrors state)
                 │  service-role writes
                 ▼
  Supabase Postgres                               [EXTENDED, migration 0010]
     • tenants (+ stripe/subscription columns)
     • plan_tiers · products · prices · subscriptions · stripe_events · leads
                 ▲
                 │  reads key_quota / subscription_status
  FastAPI BFF (Railway)                           [MINIMAL CHANGE]
     • write routes gain require_active_subscription (lapsed → 402)
     • GET /v1/tenants/{t}/billing (status + usage read)
```

| Unit | Responsibility | Depends on |
|---|---|---|
| **Billing schema** (migration 0010) | Stripe columns on `tenants`; `plan_tiers`, `products`, `prices`, `subscriptions`, `stripe_events`, `leads`; RLS; `create_tenant_for_current_user` RPC | C1 `tenants` |
| **Edge Functions** (Deno) | webhook mirror (idempotent) + checkout/portal creation; the **only** Stripe I/O and the **only** writers to the mirror + `tenants` billing columns | Stripe, Supabase service role |
| **Signup funnel** (`apps/web`) | account → org → Checkout(trial) → land in app | Supabase Auth, `create-checkout-session` |
| **In-app billing surface** (`apps/web`) | `/billing` plan/usage → Portal; signup wizard; subscription banners | BFF `/billing`, `create-portal-link` |
| **Marketing site** (Astro, `apps/site`) | home/product/pricing/docs(=connector spec)/security/contact + book-a-demo | mirrored prices (pricing), shared design tokens, `leads` |

**The load-bearing seam:** Edge Functions own all Stripe I/O and are the sole writers to the billing mirror + `tenants` billing columns; everyone else *reads* mirrored state. Quota enforcement stays where C3 put it (ingest reads `key_quota`); the webhook keeps that number correct per plan.

---

## 2. Data model — migration `0010`

Follows existing conventions: RLS on every new table; `trax_app`/`trax_seed`/service-role only; **no member writes to billing state**.

### 2.1 `tenants` — add billing columns

Denormalized so the hot-path enforcement read is a single-row lookup (no join); the BFF already loads the tenant row to resolve `tenant_uuid`.

| Column | Type | Purpose |
|---|---|---|
| `stripe_customer_id` | `text unique` | the org's Stripe Customer; null until first Checkout |
| `stripe_subscription_id` | `text` | the active subscription id |
| `subscription_status` | `subscription_status` (enum) | denormalized from the active sub — the read-only-gate signal; null = never subscribed |
| `current_period_end` | `timestamptz` | renewal/period display |
| `trial_ends_at` | `timestamptz` | trial countdown |

`plan_tier` + `key_quota` already exist (C1) and become **webhook-owned**. There is no member-facing UPDATE policy on `tenants` (C1), so a tenant can never self-edit quota/plan — only the webhook (service role) can.

### 2.2 `plan_tiers` — DB-authoritative tier→quota map

```
plan_tiers(tier text primary key, key_quota int not null, display_name text, sort int)
```
Seeded `starter=5000, growth=25000, scale=100000` (enterprise is contact-us, not self-serve). Public-readable (drives the pricing-page bands). The quota number is DB-authoritative rather than trusted from free-text Stripe metadata; a price binds to a tier via `price.metadata.tier`.

### 2.3 Stripe mirror (the `supabase-community/nextjs-subscription-payments` shape, **tenant-billed** not user-billed)

- `subscription_status` **enum**: `trialing, active, past_due, canceled, incomplete, incomplete_expired, unpaid, paused`.
- `products(id text pk, active bool, name text, description text, metadata jsonb)` — Stripe product ids.
- `prices(id text pk, product_id text → products, active bool, unit_amount bigint, currency text, interval text, interval_count int, trial_period_days int, metadata jsonb)` — `metadata.tier` binds a price to a `plan_tiers` row.
- `subscriptions(id text pk, tenant_id uuid → tenants, status subscription_status, price_id text → prices, quantity int, cancel_at_period_end bool, current_period_end timestamptz, created timestamptz, trial_end timestamptz, ...)` — full detail/history for the billing page.
- `stripe_events(id text pk, type text, received_at timestamptz default now())` — webhook idempotency ledger.

### 2.4 `leads` (marketing contact / book-a-demo)

```
leads(id uuid pk default gen_random_uuid(), name text, email text, company text,
      message text, source text, created_at timestamptz default now())
```
**Insert-only** RLS for `anon` (the public form writes; nobody reads via the API). The team reads leads directly in Supabase. A honeypot field on the form + Cloudflare Turnstile are anti-spam (Turnstile is later hardening).

### 2.5 RLS

- `products`, `prices`, `plan_tiers`: **public read** (`to anon, authenticated using (active)` — `plan_tiers` always readable). No public writes. The marketing pricing page is unauthenticated.
- `subscriptions`: tenant-scoped read (`tenant_id = (select public.current_tenant_id())`, to `trax_app` + `authenticated`); no member writes.
- `leads`: `anon` **insert-only**; no select.
- `stripe_events`, all mirror writes, `tenants` billing-column writes: **service-role only** (the webhook). This makes the webhook the **second sanctioned service-role writer** alongside the C3 worker — flagged explicitly in §7 because the parent spec's rule is "no service_role outside the worker."

### 2.6 Self-serve org creation

```
public.create_tenant_for_current_user(p_name text) returns uuid
  -- SECURITY DEFINER
```
Inserts a `tenants` row (`plan_tier='trial'`, no subscription) + a `memberships(owner)` for `auth.uid()`, generates a unique slug from the name, returns the tenant id. C1's RLS deliberately blocks direct member `tenants` inserts; this scoped, create-for-self function is the sanctioned exception. Granted `execute` to `authenticated`.

### 2.7 Webhook write contract

On `customer.subscription.created|updated|deleted`: upsert the `subscriptions` row, then set `tenants.subscription_status`, `stripe_subscription_id`, `current_period_end`, `trial_ends_at`, and — from `price.metadata.tier` → `plan_tiers.key_quota` — `plan_tier` + `key_quota`. On `checkout.session.completed`: backfill `tenants.stripe_customer_id` from `metadata.tenant_id` if unset.

---

## 3. Edge Functions (`supabase/functions/`, Deno)

Three functions sharing a `_shared/` module (service-role Supabase client, Stripe client, CORS headers for the app origin). First Deno surface in the repo; deploys via `supabase functions deploy`; independent of the Railway BFF.

### 3.1 `create-checkout-session` — POST, requires user JWT (`verify_jwt=true`)
- Verify JWT → `user`, `tenant_id` claim; confirm the user is **owner** of that tenant → else 403.
- Ensure a Stripe Customer: if `tenants.stripe_customer_id` is null, create one (`metadata.tenant_id`) and store it.
- Create a Checkout Session: `mode=subscription`, line item = `price_id`, `subscription_data.trial_period_days=14`, `payment_method_collection='always'` (card required even for trial), `customer`, `metadata.tenant_id`, `success_url`/`cancel_url` into the app. Return the session URL.

### 3.2 `create-portal-link` — POST, requires user JWT
- Verify JWT + **owner** role → load `tenants.stripe_customer_id` → create a Billing Portal session (`return_url = /billing`) → return URL. This is the entire manage-card / change-plan / cancel surface — no in-house billing UI.

### 3.3 `stripe-webhook` — POST, **public** (`--no-verify-jwt`), signature-verified
- Read raw body + `Stripe-Signature`; `constructEvent` with the signing secret → invalid ⇒ **400** (never trust an unverified body).
- **Idempotency:** if `event.id` already in `stripe_events`, return 200 immediately; else insert-then-process.
- Handlers (service-role client): `product.*`/`price.*` → upsert mirror; `customer.subscription.created|updated|deleted` → upsert `subscriptions` + sync `tenants` (§2.7); `checkout.session.completed` → backfill `stripe_customer_id`. Unhandled types → 200-acked and ignored.

**Secrets** (`supabase secrets set`): `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SIGNING_SECRET` (`SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` are auto-injected). Stripe dashboard webhook → `https://<ref>.supabase.co/functions/v1/stripe-webhook`.

---

## 4. Signup funnel & enforcement

### 4.1 Flow (wizard lives in `apps/web`; the Astro site only links in with `?plan=<tier>`)

1. **Create account** — Supabase Auth email/password (if already logged in, skip — an existing user can create an additional org).
2. **Confirm email** — confirmation stays **required** (`mailer_autoconfirm` off, deferred to here in C2). The wizard shows a "confirm your email, then continue" interstitial; steps 3–4 run once the confirmed session exists. The card requirement already blocks most abuse; requiring a real inbox is the standard B2B posture.
3. **Name organization** — `create_tenant_for_current_user(name)` → `tenants(plan_tier='trial')` + `memberships(owner)`.
4. **Refresh session** — so the JWT carries the new `tenant_id`/`tenant_role` claim (the C2 claims hook mints it from the owner membership).
5. **Checkout** — the wizard resolves the `?plan=<tier>` param to that tier's active `prices` from the public `prices` mirror and lets the user pick **monthly or annual** (monthly default); then `create-checkout-session` with the chosen `price_id` → redirect to Stripe (card + 14-day trial).
6. **Return** — `success_url` → in-app **first-upload checklist** (points at Data & Connections). `cancel_url` → `/billing` in a "finish subscribing to start" state (org exists, no active sub).

### 4.2 Status → capability

The webhook keeps `tenants.subscription_status` current; everything derives from it.

| App state | `subscription_status` | Capability |
|---|---|---|
| **Provisioning** | none / `incomplete` / `incomplete_expired` | reads OK; writes blocked → "finish subscribing" |
| **Active** | `trialing`, `active`, `past_due` | full read + write (`past_due` shows "update card" but stays writable through Stripe's dunning — degrade, don't freeze) |
| **Read-only** | `canceled`, `unpaid`, `paused` | reads only → "reactivate" → Portal |

### 4.3 Three enforcement gates

1. **Over-quota (ingest, already built C3):** ingest counts distinct part-location keys vs `key_quota` → over ⇒ job fails. C4 makes `key_quota` plan-driven (webhook) and surfaces an **"Upgrade" CTA** on that error → `/billing`.
2. **Write-gate (BFF, new):** a `require_active_subscription` dependency on the BFF's write routers (approve / reject / defer / bulk / killswitch / rollback / scenarios + C3's uploads / ingest-create) reads `subscription_status` and returns **HTTP 402** unless status ∈ {`trialing`,`active`,`past_due`}. **Read routes are never gated** — planners keep full access to their own history mid-lapse.
3. **Frontend banners:** trial countdown · `past_due` update-card · read-only reactivate — all → Portal.

### 4.4 Trial abandonment

An org whose owner bailed at Checkout sits in *Provisioning*: reads work, writes prompt "finish subscribing," `/billing` offers "Start subscription" (re-invokes `create-checkout-session`). No orphan cleanup — it's a tenant with no active sub.

---

## 5. Marketing site (`apps/site`, Astro)

A second Vercel project at the **brand apex** (the app stays at `app.<brand>`). Astro ships ~zero JS by default; interactive bits are React islands so the design system carries over.

**Design-system reuse (small in-scope refactor):** extract the app's Tailwind theme tokens (the Airvoyant palette/fonts from `apps/web`'s `tailwind.config` + `globals.css`) into a **shared Tailwind preset** both `apps/web` and `apps/site` consume — brand consistency becomes automatic.

**Pages:**
- **Home** — the three research-grounded differentiators (native eMRO depth · governed-autonomy write-back · BVR savings attribution), primary CTA "Start free trial".
- **Product** — the recommend → govern → act loop, screenshots.
- **Pricing** — self-serve tiers. **Build-time fetch:** Astro reads public `plan_tiers` + active `prices` (anon key, public-read RLS) in frontmatter and bakes them into static HTML (crawlable, fast). A price change is rare and triggers a redeploy. Each tier's "Start free trial" → `app.<brand>/signup?plan=<tier>`.
- **Docs** — the **canonical upload format = the public connector spec** (C3's 6-file/column contract), authored as MDX mirroring `canonical.py`, with a line stating it *is* the validator contract.
- **Security** — RLS tenant isolation, SOC 2 posture, encryption, audit ledger.
- **Contact / Book-a-demo** — React-island form → `leads` table via the anon client (insert-only RLS) + honeypot; Turnstile later.

**SEO:** per-page meta/OG, `sitemap.xml`, `robots.txt` (Astro-native). Analytics optional, not core.

**Deploy & domain:** own Vercel project, **CLI-deployed from `apps/site`** (or Git-integration with Root Directory set to `apps/site` — never root-built; see the today-dated Vercel lesson). Design for `<brand>` apex (marketing) + `app.<brand>` (app); the concrete hostname is confirmed at rollout (`aeronta.app` currently resolves to another Vercel project, so the exact domain is a rollout decision).

---

## 6. In-app billing surface (`apps/web`)

Reads go through the **BFF** (uniform with every other app read); Stripe actions go to the **Edge Functions**.

- **`GET /v1/tenants/{t}/billing`** (new BFF read) → `{plan_tier, subscription_status, key_quota, keys_used, current_period_end, trial_ends_at}`, where `keys_used` = the tenant's `part_keys` count (the BFF has the pool). A `useSubscription` hook wraps it.
- **`/billing` page** (owner-gated in nav, like Members is admin+): current plan + status badge, trial countdown / renewal, a **usage meter** (`keys_used` vs `key_quota`, over-quota warning), **"Manage billing"** → `create-portal-link` → Portal. In *Provisioning* it shows **"Start subscription"** → `create-checkout-session` instead.
- **`/signup` wizard** — the §4.1 flow; reads `?plan=<tier>`.
- **Global banners** (App shell, from `useSubscription`): trial days-left · `past_due` update-card · read-only reactivate. The C3 over-quota ingest error gains the "Upgrade" CTA.

---

## 7. Security, error handling, testing

**Security**
- The `stripe-webhook` Edge Function is the **second sanctioned service-role writer** (alongside the C3 worker). It writes **only** the billing mirror tables + `tenants` billing columns. No other service-role use is introduced.
- Webhook bodies are untrusted until `constructEvent` verifies the Stripe signature; an invalid signature is 400 with no write.
- Members cannot write any billing state (no UPDATE policy on `tenants`; mirror writes are service-role only) — a tenant cannot self-upgrade its quota.
- Secrets live in platform stores (Supabase function secrets, Vercel/Railway env), never the repo.
- `create_tenant_for_current_user` is `SECURITY DEFINER` but strictly create-for-`auth.uid()`; the `leads` table is insert-only for `anon`.

**Error handling**
- Checkout/portal function errors → structured JSON + non-2xx; the app surfaces a retry.
- Webhook: idempotent via `stripe_events` dedup; unhandled event types are acked and ignored; a processing error returns non-2xx so Stripe retries.
- BFF write-gate: **402** with an "upgrade/reactivate" detail, distinct from 401/403.
- Over-quota ingest failure carries the upgrade CTA.

**Testing**
- **Postgres/RLS:** mirror isolation (member reads only own `subscriptions`; `products`/`prices`/`plan_tiers` public-read; members can't write billing columns); `create_tenant_for_current_user` (creates tenant+owner for the caller only, unique slug, can't create for others); `leads` insert-only.
- **Edge Functions (`deno test`):** webhook idempotency (dup `event.id` → one write), signature rejection (bad sig → 400, no write), tier→quota sync (`growth`-metadata price → `key_quota=25000`), checkout/portal owner-gate (non-owner → 403), with a stubbed Stripe client.
- **BFF:** the `require_active_subscription` **402 matrix** across all statuses (writes gated, reads never), `/billing` response shape, over-quota surfacing.
- **apps/web (Vitest):** signup wizard steps, `/billing` states (provisioning/active/past_due/read-only), banners, usage meter.
- **apps/site:** build passes; pricing renders from `plan_tiers`.
- **One Playwright e2e** (the parent spec's headline gate): signup → Stripe **test-mode** checkout (test card) → upload → recommendation appears → approve.

**Rollout**
- Stripe **test mode** throughout dev/CI (`stripe listen --forward-to` the local function).
- Create Products/Prices (test → live), `supabase secrets set` the Stripe keys, register the webhook at the functions URL, `supabase functions deploy` the three functions, deploy `apps/site` (CLI, correct root), wire the domain.
- Live billing smoke (extend `deploy/aeronta_smoke.py`, env-gated): sign up → test-checkout → assert the webhook applied `plan_tier`/`key_quota`.

---

## 8. Task-group decomposition

Ships as one C4; the plan sequences into six independently-testable groups:

1. **Data model** — migration 0010 (tenants columns, `plan_tiers`, mirror tables, enum, `stripe_events`, `leads`, RLS, `create_tenant_for_current_user`) + RLS tests.
2. **Edge Functions** — `_shared`, `create-checkout-session`, `create-portal-link`, `stripe-webhook` + `deno test`.
3. **BFF** — `require_active_subscription` write-gate (402) + `GET /v1/tenants/{t}/billing` + tests.
4. **apps/web** — `/signup` wizard, `/billing` page, `useSubscription`, banners, over-quota upgrade CTA + Vitest.
5. **apps/site** — shared Tailwind preset, Astro pages (home/product/pricing/docs/security/contact), pricing build-time fetch, `leads` form.
6. **Rollout + live smoke** — Stripe products/prices, secrets, webhook registration, function + site deploy, domain, `aeronta_smoke.py` billing stage.

Groups 1→2→3 are the billing spine (sequential); 4 depends on 2+3; 5 is largely independent (needs 1 for prices); 6 is last.

---

## 9. Out of scope / deferred

- **Enterprise self-serve** — Enterprise is contact-us only (the `leads` path); SAML SSO, connector work, and outcome-linked (%-of-savings) pricing are post-C4 (the research confirms Stripe *can* express usage/outcome billing, but self-serve GA of the advanced Metronome models is sales-led).
- **Final price points** — the system is price-agnostic; dollar amounts are set in Stripe at rollout by the owner.
- **Stripe → deploy-hook** auto-rebuild of the pricing page on price change — manual redeploy is fine for v1 (prices change rarely).
- **Lead notifications / CRM** — leads land in Supabase; email/Slack/CRM routing is later.
- **Dunning/email customization, tax (Stripe Tax), invoicing UI** — handled by Stripe defaults + the Customer Portal in v1.
- **Annual-vs-monthly toggle polish, coupons/promo codes** — Stripe supports them; the pricing page ships monthly+annual, promos later.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Webhook missed/replayed → mirror drift | `stripe_events` idempotency + Stripe's own retry; the Portal + a manual re-sync are the backstop; subscription reads tolerate a null/stale status by treating unknown as Provisioning (safe-closed for writes) |
| A tenant self-upgrades quota | impossible by construction — no member write path to `tenants`/mirror; quota is webhook-owned from `plan_tiers` |
| Trial abuse (free compute) | card required at trial; email confirmation required; per-tenant `key_quota` caps compute |
| Second Vercel project repeats the root-build 404 incident | `apps/site` deploys via CLI from its own dir, or Git-integration with Root Directory = `apps/site` — documented from the C3-era incident |
| Edge Functions are a new Deno toolchain | isolated to `supabase/functions/`; `deno test` in CI; no impact on the Python/TS suites |
