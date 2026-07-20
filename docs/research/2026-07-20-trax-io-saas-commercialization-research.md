# Trax IO → Commercial Multi-Tenant SaaS on Vercel + Supabase — Research Report

**Date:** 2026-07-20
**Owner:** Miguel Sosa, VP Head of Innovation
**Method:** Deep-research harness — 5 search angles, 21 sources fetched, 104 claims extracted, 25 adversarially verified (3-vote refutation panels): 24 confirmed, 1 refuted. All Vercel/Supabase pricing and limits verified against docs updated within ~3 weeks of this date.
**Question:** How to commercialize Trax IO (Python FastAPI BFF + React/Vite frontend + heavy ML compute over ~59K PN×Location keys) into a revenue-generating multi-tenant B2B SaaS — own sales website, login, user management — deployed on Vercel + Supabase, with AMOS/Trax/other connectors later.

---

## Headline

The stack works with **one decisive architectural split**: the Next.js sales site, the app frontend, and (in principle) the FastAPI BFF can live on Vercel — but the numpy/scipy/numba/statsforecast compute tier **cannot**. Vercel Functions are hard-capped at 4 GB / 2 vCPU, 800s GA duration, and a 500 MB Python bundle; the ML engine must run on an external container platform. Supabase is a credible enterprise B2B backbone: RLS-enforced tenant isolation + RBAC is its official pattern, and per-airline SAML SSO is available from the $25/mo Pro plan at $0.015/SSO-MAU — roughly two orders of magnitude cheaper per customer org than WorkOS/Auth0.

---

## Verified findings

### 1. Multi-tenancy on Supabase (confidence: high)

- **The official pattern** is shared-database/shared-schema: a `tenant_id` column on every table + Postgres Row Level Security enforcing isolation at the database layer + RBAC per role. A Supabase maintainer recommends this over per-tenant databases or schemas.
- **Supabase's "no data leakage risk" marketing is qualified by real bypass vectors** the implementation must explicitly defend against: `service_role` key bypasses RLS entirely (never in client code), security-definer views leak, tables without policies are exposed via the auto-generated API, and `user_metadata` JWT claims are user-spoofable (tenant claims must live in `app_metadata` or be joined server-side).
- **RLS performance discipline:** wrap `auth.uid()` in a scalar subquery (`(select auth.uid())`) and index every column referenced in RLS policies — documented as the difference between multi-minute and millisecond queries at scale.
- Sources: [supabase.com/solutions/b2b-saas](https://supabase.com/solutions/b2b-saas), [RLS docs](https://supabase.com/docs/guides/database/postgres/row-level-security), [Makerkit RLS best practices](https://makerkit.dev/blog/tutorials/supabase-rls-best-practices). Votes: 3-0, 3-0.

### 2. Auth & user management (confidence: high)

- **Supabase Auth covers the full surface Trax IO needs:** email/password, magic links, OAuth social, phone OTP, and **SAML 2.0 enterprise SSO from the Pro plan ($25/mo)** at $0.015 per SSO monthly-active-user beyond a 50-MAU quota. Platform tiers: Free $0 / Pro $25 / Team $599 / Enterprise custom.
  - *Corrected during verification (0-3 refutation):* SAML does **not** require the $599 Team plan — earlier drafts of the research overstated the SSO floor by ~24×.
- **Cost comparison:** a 100-seat airline org costs ~$1.50/mo in Supabase SSO overage vs $125/mo for a single WorkOS connection — this defers the point at which Trax IO outgrows Supabase Auth by a wide margin.
- **The multi-tenant SSO mechanism is concrete and documented:** register one SAML connection per customer IdP (CLI/Admin API — not the dashboard); each connection's unique `sso_provider_id` is embedded in the user's JWT and readable via `auth.jwt()` in RLS policies — i.e., each airline's corporate IdP directly drives that tenant's data isolation. Supabase's docs include a literal RLS policy example for exactly this.
- Sources: [SAML SSO docs](https://supabase.com/docs/guides/auth/enterprise-sso/auth-sso-saml), [pricing](https://supabase.com/pricing), [workos.com/pricing](https://workos.com/pricing). Votes: 3-0 ×4 + 3-0.

### 3. Compute placement — the critical split (confidence: high)

- **The ML recompute engine cannot run on Vercel.** Hard ceilings (verified against the limitations doc updated 2026-07-01):
  - Duration: 300s default, **800s max GA** on Pro/Enterprise (1800s only in beta; overrun → 504 `FUNCTION_INVOCATION_TIMEOUT`)
  - Memory/CPU: **4 GB / 2 vCPU maximum even on Enterprise**
  - Python bundle: **500 MB uncompressed**, no tree-shaking; the 5 GB "Large Functions" option is public beta, requires Fluid compute, and is incompatible with Secure Compute and Static IPs. The numpy/scipy/numba(llvmlite)/statsforecast/pandas tree plausibly totals ~350–450 MB installed — brushing the cap before app code.
  - Batch recompute over ~59K keys is memory- and duration-bound in exactly the dimensions Vercel caps. Vercel's own docs direct such workloads elsewhere (Vercel Workflows / external compute).
- **The FastAPI BFF is hostable on Vercel in principle:** the Python runtime natively runs ASGI apps with FastAPI presets; a deployed FastAPI app becomes a single Vercel Function on Fluid compute; and Vercel **Services** deploys a Python API alongside a Next.js frontend in one project with shared routing/domain. Caveat that matters for Trax IO: Functions semantics permit no long-lived in-memory state — the current `PlannerStore` (~14s snapshot boot, all state in process memory) would need rework (state → Supabase Postgres) before the BFF could move there. Until then, the pragmatic path is BFF + engine together on a container platform.
- Sources: [functions/limitations](https://vercel.com/docs/functions/limitations), [FastAPI on Vercel](https://vercel.com/docs/frameworks/backend/fastapi), [Python runtime](https://vercel.com/docs/functions/runtimes/python), [Services](https://vercel.com/docs/services). Votes: 3-0 ×6, 2-1.

### 4. Billing (confidence: high / medium)

- **The reference pattern is webhook-driven state sync:** Stripe is the source of truth for products, prices, and subscription status, mirrored into Supabase Postgres via webhooks (`product/price/subscription` created-updated-deleted), with feature access gated by RLS keyed on subscription status.
- **The canonical starter is dead:** `vercel/nextjs-subscription-payments` was archived 2025-01-23, and its official replacement (`nextjs/saas-starter`) **drops Supabase entirely** (Postgres/Drizzle + custom JWT auth). Implement the webhook pattern directly or from the maintained `supabase-community/nextjs-subscription-payments` fork — don't treat either Vercel repo as current reference. (High confidence, 3-0 ×2.)
- **Usage/outcome pricing is buildable on Stripe without a custom metering stack** (medium confidence, 2-1): Stripe Billing's usage-based product (powered by the Metronome acquisition, completed 2026-01-14) supports pay-as-you-go, subscription+overage, credit burndown, outcome-based, and multidimensional pricing — i.e., per-part-key or savings-linked billing is expressible. Dissent centered on GA availability: the advanced models sit in a private-preview / sales-led tier, and Trax IO must compute and emit the savings figure itself (the BVR pipeline already does exactly this).
- Sources: [archived starter](https://github.com/vercel/nextjs-subscription-payments), [replacement](https://github.com/nextjs/saas-starter), [community fork](https://github.com/supabase-community/nextjs-subscription-payments), [Stripe usage-based billing](https://stripe.com/billing/usage-based-billing).

### 5. Competition & integration surfaces (confidence: high)

- **Armac Systems' RIOsys is a head-on competitor, not adjacent:** airline-targeted MRO inventory optimization computing optimal stocking for rotables and consumables under intermittent demand across multi-site operations — the same problem space, deliverables, and buyers as Trax IO's ROP/EOQ/safety-stock recomputation. Majority-owned by **Lufthansa Technik**, historically SAP-integration-heavy, M&E-system-agnostic, with named deployments at Iberia (via SR Technics) and Cathay Pacific. (3-0 ×2.)
- **The market is consolidating into closed-loop forecast-to-procure:** in June 2026, SkySelect (AI parts procurement, $9M raise in March 2026) and Armac announced a platform integration spanning forecasting → purchase-requirement generation → procure-to-pay execution. Standalone optimization-only positioning now faces competitors that close the loop into procurement. (Caveat: vendor press release; one partnership is a single data point.) (3-0.)
- **ATA Spec 2000 is the vendor-neutral connector target:** governed by the ATA e-Business Program (under Airlines for America) with airlines, OEMs, distributors, lessors, MROs, and tech providers — not any single vendor. Of its 18 chapters, the ones mapping directly onto Trax IO's flows: **ch. 1 Provisioning, ch. 2 Procurement Planning, ch. 3-4/6 Materiel Management, ch. 7 Repair Order Administration, ch. 11 Reliability Data, ch. 12 Airline Inventory Redistribution (AIRS)**. Ch. 5 is discontinued; chapters are actively revised — target current revisions at build time. (3-0 ×2.)
- Sources: [RIOsys](https://armacsystems.com/solutions/riosys-software/), [SkySelect/Armac press release](https://www.aviationpros.com/aircraft-maintenance-technology/engines-parts/press-release/55382723/skyselect-partners-with-armac-systems-to-optimize-maintenance-inventory-planning), [AviTrader](https://avitrader.com/2026/06/08/skyselect-and-armac-target-mro-procurement-gap/), [ataebiz.org/spec-2000](https://ataebiz.org/spec-2000/).

---

## Refuted during verification

| Claim | Vote | Correction |
|---|---|---|
| "SAML SSO requires at least the Team plan ($599/mo)" | 0-3 | SAML 2.0 is available from the **Pro plan ($25/mo)** — the SSO cost floor is ~24× lower than the refuted claim asserted. |

---

## Caveats

1. **Vendor marketing as evidence:** Supabase's "no data leakage risk," Armac's product-scope description, and the SkySelect/Armac release are vendor-authored; corroborated where possible, but RLS safety/performance at 59K-key multi-airline scale was not independently benchmarked.
2. **Beta-status time sensitivity:** the two Vercel escape hatches most relevant here — 1800s duration and 5 GB Python bundles — are both beta as of July 2026 and incompatible with Secure Compute/Static IPs. Recheck at build time.
3. **The Stripe outcome-billing finding survived 2-1** — capability is solid, self-serve GA of the advanced Metronome models is uncertain (may require sales-led engagement with Stripe).
4. **The official Next.js SaaS billing template no longer uses Supabase** — the billing pattern must be assembled from the community fork or first principles.

## Open questions (no claims survived verification)

1. **Aviation MRO pricing benchmarks** — actual price points and contract structures for RIOsys / Servigistics / AVIATAR (per-tail, per-part-key, per-seat, %-of-verified-savings) and acceptable deal sizes for a sales-led motion.
2. **Compute-tier platform choice** — Fly.io vs Railway vs Render vs AWS ECS/Fargate for the Docker-packaged engine + BFF, including private networking back to the Vercel frontend and the ~14s snapshot-boot constraint. Needs a dedicated cost/ops/latency analysis.
3. **AMOS / Ramco / IFS-Ultramain integration surfaces** — what REST APIs, Spec 2000 message support, or export jobs they actually expose, and whether Spec 2500 applies — i.e., can the connector roadmap standardize on Spec 2000 chapters or will it need per-vendor adapters like the existing eMRO one.
4. **The Supabase Auth ceiling** — at what customer count or compliance threshold (SOC 2 evidence, SCIM provisioning, audit-grade session controls) enterprise airline procurement forces a WorkOS/Auth0 migration, and how reversible that is once Supabase JWTs are wired into RLS from day one.

---

## Recommended target architecture (synthesis)

```
Vercel
├── Next.js sales/marketing site  (new — the commercial front door)
├── App frontend                  (apps/web migrated or served static)
└── (later) FastAPI BFF via Vercel Services — only after PlannerStore
    state moves out of process memory into Supabase Postgres

Supabase (one project, shared schema + tenant_id + RLS)
├── Auth: email/password + per-airline SAML (sso_provider_id → RLS)
├── Tenant registry, orgs/roles (RBAC), app_metadata tenant claims
├── Planner state: queue, decisions, writeback ledger (replaces in-memory PlannerStore)
└── Billing mirror (Stripe webhooks → products/prices/subscriptions tables)

Container platform (Fly.io / Railway / ECS — open question #2)
├── FastAPI BFF (initially, unchanged Docker packaging)
└── ML compute engine (numpy/scipy/numba/statsforecast — cannot live on Vercel)

Stripe
└── Subscriptions + seats now; per-key/outcome (savings-linked) later via
    usage-based billing — BVR pipeline already computes the savings figure
```

**Positioning note:** against Armac/SkySelect's closed-loop consolidation, Trax IO's differentiators to lead with are (a) native eMRO depth (the connector already exists), (b) the governed-autonomy write-back loop (tiers, guardrails, audit ledger — competitors recommend; Trax IO *acts*), and (c) the BVR savings-attribution pipeline, which is precisely the artifact outcome-based pricing requires.
