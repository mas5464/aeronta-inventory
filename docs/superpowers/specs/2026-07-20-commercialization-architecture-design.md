# Commercialization Architecture — Standalone Multi-Tenant SaaS on Vercel + Supabase + Railway

**Date:** 2026-07-20
**Status:** Approved (brainstorm complete; this is the umbrella architecture spec — each sub-project C1–C4 gets its own plan)
**Owner:** Miguel Sosa
**Grounding:** [docs/research/2026-07-20-trax-io-saas-commercialization-research.md](../../research/2026-07-20-trax-io-saas-commercialization-research.md) (adversarially-verified platform limits, pricing, and patterns cited below)

---

## 1. Decisions locked (user-approved)

| Decision | Choice |
|---|---|
| First milestone | **Full self-serve SaaS** — auth, tenancy, provisioning, and billing all in v1 scope |
| Commercial identity | **Standalone product, new brand: "Aeronta Inventory"** (name locked by owner 2026-07-21; supersedes the deferred-codename note). System-agnostic core; eMRO, AMOS, Trax, Ramco are all connectors. Cloud projects (Supabase/Vercel/Railway) carry the Aeronta name |
| Data intake | **CSV/Excel file upload, self-serve.** The upload schema is canonical model v1 and doubles as the future connector spec |
| Pricing structure | **Flat monthly/annual tiers banded by managed part-location keys** + Enterprise contact-us tier. Exact price points deferred to sub-project C4; the structure (what we meter: distinct part-location keys at ingest) is locked now |
| Compute host | **Railway** for the FastAPI BFF + ML engine containers (Fly.io is the named fallback if Railway limits surface during C2's load test). AWS migration is a later enterprise-compliance option, not a v1 concern |
| Overall approach | **"Wrap and persist"** — keep the tested Python backend nearly intact; commercialize around it. Rejected: Next.js full rewrite (discards ~1,100 passing tests before revenue), canonical-model-first rebuild (slowest to revenue, model designed in a vacuum) |

## 2. System architecture

```
                        ┌─ Vercel ────────────────────────────┐
  prospect ──────────►  │  Next.js marketing site + signup    │
                        │  (new, brand domain)                │
  tenant user ───────►  │  App frontend (apps/web, Vite,      │
                        │  static) at app.<brand>.com         │
                        └──────────────┬──────────────────────┘
                                       │ HTTPS + Supabase JWT
                        ┌─ Railway ────▼──────────────────────┐
                        │  FastAPI BFF (existing container;    │
                        │  PlannerStore now backed by Postgres)│
                        │  ML engine worker (recommendation +  │
                        │  forecasting) — async jobs only      │
                        └──────────────┬──────────────────────┘
                                       │
                        ┌─ Supabase ───▼──────────────────────┐
                        │  Auth (email/pw, OAuth, per-tenant  │
                        │  SAML later) · Postgres (tenants,   │
                        │  users, planner state, uploads,     │
                        │  jobs, billing mirror — all RLS)    │
                        │  Storage (uploaded files)           │
                        └─────────────────────────────────────┘
                        Stripe ◄── webhooks ──► Supabase mirror
```

Boundaries:

- **Vercel serves only static/SSR web.** The ML compute tier cannot run there (verified hard caps: 4 GB / 2 vCPU, 800s GA duration, 500 MB Python bundle vs a ~350–450 MB numpy/scipy/numba/statsforecast tree).
- **The BFF is the only surface the app frontend talks to** — the same OpenAPI-contract pattern as today. It verifies Supabase JWTs and scopes every query by the token's tenant claim.
- **The engine never faces the internet.** It is a worker consuming jobs from a Postgres-backed queue and writing results back to Postgres.
- **Supabase Postgres is the single source of durable state.** Stripe is the source of truth for subscriptions, mirrored in via webhooks.

## 3. Tenancy & auth

- **Tables:** `tenants` (customer org; plan tier, key quota, subscription state), `memberships` (user ↔ tenant, role ∈ `owner` / `admin` / `planner` / `viewer`).
- **Claims:** active tenant + role stamped into the JWT via Supabase's custom-claims hook, in `app_metadata` only (`user_metadata` is user-spoofable — a verified RLS bypass vector).
- **RLS on every tenant-scoped table**, keyed on the JWT tenant claim, with the documented performance discipline: wrap `auth.uid()` in a scalar subquery, index every column referenced in a policy.
- **`service_role` key** lives only in the engine worker (for cross-tenant admin jobs). Never in the BFF request path, never client-adjacent.
- **Auth methods at launch:** email/password + Google/Microsoft OAuth. Per-airline SAML SSO added per-customer later (available from Supabase Pro at $0.015/SSO-MAU; each connection's `sso_provider_id` feeds the same claims hook).
- **Role mapping to existing app semantics:** approve/reject/defer = `planner`+; kill switch + members management = `admin`+; billing = `owner`.
- **4-layer isolation, restated for the new stack:** JWT claim (contract) → BFF tenant scoping (agent) → RLS (data) → per-tenant Stripe subscription + quota (commercial).

## 4. Persistence rework — Postgres `PlannerStore` (the load-bearing change)

- Replace the in-memory, snapshot-booted `PlannerStore` with a Postgres-backed implementation in Supabase. Tables (all tenant-scoped, RLS'd): `recommendations` (queue: status, tier, priority, provenance JSON), `decisions` (approve/reject/defer audit trail), `writeback_ledger` (existing history/rollback ledger, now durable), `part_keys` + `feature_snapshots` (feature data serving drill-down/dashboard reads), `kill_switches`, `bvr_cache`.
- **The `PlannerStore` interface is the seam:** same methods, Postgres implementation behind them. The OpenAPI contract is unchanged, so `apps/web` and its 288 tests are untouched. The in-memory implementation survives for unit tests.
- Heavy engine intermediates (pooling matrices, forecast internals) stay in worker memory during a run; only inputs and results persist.
- The BFF becomes stateless and horizontally scalable; boot time stops mattering; a recompute publishes rows instead of requiring a snapshot reload.
- **C1 correction (2026-07-20):** `dashboard()`'s `open_recommendations`/`net_cost_impact` fields turned out to be **entry-existence-keyed**, not `status='pending'`-filtered as originally guessed above — they're invariant across every decision verb, so `PgPlannerStore.dashboard()` is a pure static-snapshot read with no live SQL recompute (see plan [Task 11](../plans/2026-07-20-c1-supabase-foundation.md)).

## 5. Data intake — canonical model v1

- **Canonical model v1 = the upload schema:** ~6 documented files (parts master, locations, current stock levels, demand history, open orders, vendor lead times/prices) with required + optional columns — a deliberate simplification of the 21 eMRO extract queries down to the minimum for credible recommendations. Exact column contract is defined in sub-project C3.
- **Flow:** upload (CSV/Excel) → Supabase Storage → `ingest` job validates + maps to the extract format the engine already consumes → engine run → recommendations appear in the queue.
- **Validation is per-row and surfaced in-app** ("demand_history row 214: unknown location code MIA-2") — never silent drops.
- **The eMRO extract becomes mapper #1** — a thin adapter proving the connector pattern; AMOS/Trax/Ramco connectors are later mappers targeting the same canonical model.
- **Quota enforcement lives at ingest:** distinct part-location keys counted against the tenant's plan tier; over-quota blocks the run with an upgrade prompt.

## 6. Compute tier & jobs

- **Two containers on Railway:** BFF (web-facing) and engine worker. Existing Dockerfiles deploy near-as-is.
- **Job queue = a Postgres `jobs` table** (kinds: `ingest`, `recompute`, `bvr`), claimed via `FOR UPDATE SKIP LOCKED`. No Redis/Kafka — deliberately boring, sufficient at this scale.
- **Cadence:** on-demand recompute after each ingest + nightly scheduled run per active tenant. Self-serve tenants (≤5K–25K keys) run in minutes; the 59K-key reference dataset takes tens of minutes and remains the internal scale benchmark.
- **Networking:** frontend → BFF over HTTPS with JWT; worker ↔ BFF share nothing directly (Postgres is the interface); Supabase access via connection pooler with per-service credentials.

## 7. Billing (Stripe)

- **Plans:** three self-serve tiers banded by managed part-location keys — working bands Starter ≤5K, Growth ≤25K, Scale ≤100K — plus Enterprise (contact-us: SAML, connector work, outcome-linked pricing later). Monthly + annual.
- **Pattern:** webhook-driven mirror — Stripe Checkout for signup, Stripe Customer Portal for card/plan/cancel (minimal in-house billing UI), one idempotent webhook endpoint syncing `products` / `prices` / `subscriptions` into Supabase. Built from the maintained `supabase-community/nextjs-subscription-payments` pattern; the archived Vercel starter is explicitly not the reference.
- **Enforcement:** plan + quota on the `tenants` row, updated by webhook; ingest checks quota; **a lapsed subscription degrades to read-only, never lockout** — planners keep access to their own history mid-renewal.
- **Trial:** 14-day free trial on any tier, card required.

## 8. Marketing site & signup

- **Next.js on Vercel, brand domain.** Pages: home, product, pricing, docs (the upload-format spec lives here — it is also the public connector spec), security, contact.
- **Signup flow:** create account (Supabase Auth) → name organization (creates `tenants` row + `owner` membership) → Stripe Checkout with trial → land at `app.<brand>.com` with a guided first-upload checklist.
- **No demo sandbox in v1** (upload-first decision); a "book a demo" path covers prospects who want a full walkthrough.
- **`apps/web` gains an auth shell:** login/logout, tenant switcher, members & roles page, plan/usage page linking to the Stripe portal. All other views unchanged.

## 9. Security, error handling, testing

**Security**
- RLS is the hard data boundary, with explicit defenses for the verified bypass vectors: no `service_role` outside the worker; no security-definer views without review; claims only in `app_metadata`; every new table ships with a policy or is explicitly `private` schema.
- Uploaded files are untrusted input: size caps, content-type checks, defensive parsing.
- Secrets in platform stores (Vercel/Railway/Supabase env), never in the repo.
- `decisions` and `writeback_ledger` remain append-only (audit posture carried over from v1's SOC 2 groundwork).

**Error handling**
- Ingest failures: per-row, surfaced in-app.
- Job failures: retry ×3 → dead-letter state visible in an internal admin view.
- Stripe webhooks: idempotent via event-id dedup (same pattern as the existing Kafka writeback service).

**Testing**
- The existing ~1,100 tests keep passing untouched; the OpenAPI contract is the regression gate.
- New surfaces: Postgres `PlannerStore` (against real Postgres in CI, mirroring the Quarkus Dev Services pattern), RLS policies (two-tenant isolation tests — the existing 4-layer convention), ingest validation, webhook idempotency.
- One Playwright e2e: signup → upload → recommendation appears → approve.

## 10. Sub-project decomposition

| # | Sub-project | Delivers | Depends on |
|---|---|---|---|
| C1 | **Supabase foundation** — shipped 2026-07-20 (plan: [docs/superpowers/plans/2026-07-20-c1-supabase-foundation.md](../plans/2026-07-20-c1-supabase-foundation.md)) | Tenant/membership schema, RLS + claims hook, Postgres `PlannerStore` behind the existing interface | — |
| C2 | **Cloud deploy** — shipped 2026-07-21 (design: [docs/superpowers/specs/2026-07-21-c2-cloud-deploy-design.md](2026-07-21-c2-cloud-deploy-design.md); live: aeronta-inventory.vercel.app) | BFF + worker on Railway, `apps/web` on Vercel with auth shell + login | C1 |
| C3 | **Upload intake** | Canonical model v1 column contract, upload UI, ingest job + validation, quota enforcement | C1, C2 |
| C4 | **Billing + marketing site** | Stripe tiers/webhooks/portal, Next.js site, self-serve signup funnel | C1–C3 |

Each sub-project gets its own spec → plan → implementation cycle. **C1 is first** — riskiest and most load-bearing; everything sits on the tenancy schema.

## 11. Out of scope for v1 (explicitly deferred)

- MRO-system connectors (AMOS, Trax eMRO live writeback, Ramco) — the canonical model + eMRO mapper prepare for them; live bidirectional sync is post-v1.
- Demo sandbox tenant; SCIM provisioning; outcome-linked (%-of-savings) billing; SOC 2 attestation (posture maintained, audit deferred); AWS migration; Vercel-hosted BFF (possible later once stateless, via Vercel Services).

## 12. Risks

| Risk | Mitigation |
|---|---|
| Postgres `PlannerStore` query performance vs in-memory dict reads at 100K keys | Interface seam allows caching layer; RLS perf discipline from day one; 59K-key dataset is the benchmark gate in C1 |
| RLS misconfiguration leaks tenant data | Two-tenant isolation tests required for every new table; CI check that every tenant-scoped table has a policy |
| Self-serve tenants upload garbage data → bad recommendations → churn | Per-row validation with actionable errors; minimum-data thresholds before the engine will run; confidence surfacing already exists in the app |
| Brand/name decided late causes rework | Codename isolated to config + copy; no brand strings in schema or API paths |
| Railway/Fly platform limits discovered late | C2 includes a load test of the reference dataset before C3 builds on it |
