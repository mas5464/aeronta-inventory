# C4 Rollout Runbook — Billing & Marketing Site

Controller-executed steps to take the C4 (Commercial SaaS billing +
`apps/site` marketing site) build from "code merged" to "live." Mirrors the
posture of C3 Task 7's live ingest run: the automatable pieces (the smoke
stage, `deploy/aeronta_smoke.py`) already exist in this repo; the live
wiring below — Stripe objects, secrets, function deploys, webhook
registration, a second Vercel project — has to be executed by a human with
access to the Stripe dashboard, the Supabase project, and Vercel.

Live facts this runbook assumes (see [supabase/README.md](../supabase/README.md)
for the full table):

| | |
|---|---|
| Supabase project | `aeronta-inventory`, ref `sluoxufnqwusmtckklnv` |
| BFF (Railway) | `https://bff-production-6568.up.railway.app` |
| `apps/web` (Vercel) | `https://aeronta-inventory.vercel.app` (production, `/v1/*` rewrite → the Railway BFF) |
| `apps/site` (Vercel) | not yet deployed — Step 6 below creates it as its **own** project |

Run every `supabase` CLI command below with `--project-ref sluoxufnqwusmtckklnv`
(or `supabase link` once, up front, so it's implicit).

---

## Step 1 — Stripe Products + Prices (test mode, then live mode)

Do this twice: once in the Stripe **test** dashboard/API key to validate
the whole flow end to end (Steps 1–8), then repeat in **live** mode once
test mode is verified.

Three tiers, matching `public.plan_tiers` (migration `20260723000010_billing_tenants.sql`):
`starter` (5,000 keys), `growth` (25,000 keys), `scale` (100,000 keys). Each
tier needs **two** Prices — monthly and annual — so 6 Prices total per
Stripe mode.

The webhook sync (`supabase/functions/stripe-webhook/sync.ts`) resolves
`tenants.plan_tier`/`key_quota` **only** from `price.metadata.tier` — a
Price without that metadata key syncs into `public.prices` but can never
move a subscriber past "provisioning" in the app. Set it on every Price:

```bash
# Repeat per tier (starter/growth/scale) and per interval (month/year).
stripe products create --name "Growth" -d "metadata[tier]=growth"
# -> prod_XXXX
stripe prices create \
  -d "product=prod_XXXX" \
  -d "currency=usd" \
  -d "unit_amount=49900" \
  -d "recurring[interval]=month" \
  -d "metadata[tier]=growth"
stripe prices create \
  -d "product=prod_XXXX" \
  -d "currency=usd" \
  -d "unit_amount=479000" \
  -d "recurring[interval]=year" \
  -d "metadata[tier]=growth"
```

(Amounts above are placeholders — use the controller's actual published
pricing.) Prefer the Stripe dashboard for the first pass if that's easier to
review before committing to real prices; the CLI form above is for
repeatable/scripted setup (e.g. re-creating the same catalog in live mode).

## Step 2 — Supabase secrets

The three Edge Functions read these via `Deno.env.get(...)`
(`supabase/functions/_shared/stripe.ts`, `create-checkout-session/index.ts`,
`stripe-webhook/index.ts`):

```bash
supabase secrets set \
  STRIPE_SECRET_KEY=sk_test_... \
  STRIPE_WEBHOOK_SIGNING_SECRET=whsec_... \
  APP_ORIGIN=https://aeronta-inventory.vercel.app \
  --project-ref sluoxufnqwusmtckklnv
```

`STRIPE_WEBHOOK_SIGNING_SECRET` isn't known until the webhook endpoint is
registered (Step 4) — set a placeholder now, `supabase secrets set` again
once Step 4 hands you the real `whsec_...`. `APP_ORIGIN` drives
`create-checkout-session`'s Checkout `success_url`/`cancel_url`
(`.../#/billing?checkout=success|cancel` — note the `#`, see the HashRouter
caveat in the checklist below) and the Edge Functions' CORS
`Access-Control-Allow-Origin` (`_shared/cors.ts`) — it must be the exact
origin `apps/web` is served from, no trailing slash.

`SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` (read by `_shared/supabase.ts`)
are Supabase's own built-in function secrets — nothing to set manually.

Repeat this whole step with the **live** key set once cutting over to live
mode (Step 1's live-mode repeat).

## Step 2.5 — Apply the C4 migrations (REQUIRED before Steps 3–8)

The live `aeronta-inventory` database has migrations **0001–0009** applied
(see [supabase/README.md](../supabase/README.md) live-facts). C4 adds
**0010–0012** (`20260723000010_billing_tenants.sql`,
`20260723000011_billing_stripe_mirror.sql`,
`20260723000012_billing_leads_and_org_rpc.sql`) — the billing columns,
`plan_tiers`, the Stripe mirror tables, `stripe_events`, `leads`, and the
`create_tenant_for_current_user` RPC. **Every later step depends on these
tables existing** (checkout persists `stripe_customer_id`, the webhook
writes the mirror, `/billing` reads `tenants` billing columns, signup calls
the RPC).

```bash
# From the repo root (supabase/ is `supabase link`ed to sluoxufnqwusmtckklnv).
# Use the SESSION POOLER URL — the direct db.<ref>.supabase.co host is
# IPv6-only (supabase/README.md live-deploy findings); pooler user format
# is postgres.<ref>. Password: AERONTA_SUPABASE_DB_PASSWORD in the
# gitignored deploy/_local_extract/aeronta-supabase.env.
supabase db push --db-url "postgresql://postgres.sluoxufnqwusmtckklnv:<DB_PASSWORD>@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
```

Verify: `select tier, key_quota from plan_tiers order by sort;` returns
starter/5000, growth/25000, scale/100000, and `\d tenants` shows
`subscription_status`.

## Step 2.6 — Grandfather the live `aeronta-demo` tenant (REQUIRED before Step 2.7)

Migration 0010 (just applied in Step 2.5) adds `tenants.subscription_status`
as a nullable column — every **existing** tenant row, including the live
demo tenant `aeronta-demo`, comes out of that migration with
`subscription_status = NULL`. The C4 BFF's `AuthMiddleware` write-gate
(`services/agent-spine/src/trax_io_spine/bff/auth.py`,
`_ACTIVE_SUBSCRIPTION_STATUSES = {"trialing", "active", "past_due"}`) treats
any other value — including `NULL` — as inactive and returns **402** on
every write. `aeronta-demo` has no real Stripe subscription (it was seeded
directly, pre-billing), so once Step 2.7 deploys the new BFF this tenant
goes read-only: `Step 8`'s ingest smoke (`AERONTA_SMOKE_INGEST=1`, which
POSTs against `aeronta-demo`) and any manual writeback testing against it
would start failing with 402s.

Grandfather it BEFORE deploying the new BFF, via the pooler as `postgres`
(same connection pattern as Step 2.5):

```bash
psql "postgresql://postgres.sluoxufnqwusmtckklnv:<DB_PASSWORD>@aws-0-us-east-1.pooler.supabase.com:5432/postgres" \
  -c "update public.tenants set subscription_status = 'active' where slug = 'aeronta-demo';"
```

(Alternatively, create a real Stripe test-mode subscription for
`aeronta-demo` — e.g. via a manual Checkout run against Step 1's test-mode
prices — and let the webhook set `subscription_status` the normal way. The
direct SQL update is the faster, deterministic option for a demo tenant that
was never meant to be billed.)

Verify: `select slug, subscription_status from tenants where slug =
'aeronta-demo';` returns `active`.

## Step 2.7 — Deploy the C4 BFF (and worker)

The BFF and worker code that ships the 402 write-gate + `/billing` route
has not been deployed since C2/C3 — redeploy both from the checkout that
has the C4 code (`railway up` uploads the **current working directory**,
so `cd` into this checkout first, per the Railway service-variable build
config already in place — see [CLAUDE.md](../CLAUDE.md)'s Railway notes:
`RAILWAY_DOCKERFILE_PATH` is pinned per-service, `bff` → `deploy/bff.Dockerfile`,
`worker` → `deploy/worker.Dockerfile`, and both share one image but need
different entrypoints, so redeploy both):

```bash
railway up -s bff
railway up -s worker
```

Verify: `curl https://bff-production-6568.up.railway.app/healthz` returns
200, and a write against a tenant with an active-ish `subscription_status`
(e.g. `aeronta-demo`, after Step 2.6) still succeeds — a stray 402 there
means Step 2.6 didn't take or the deploy picked up stale code.

## Step 3 — Deploy the Edge Functions

```bash
supabase functions deploy create-checkout-session create-portal-link \
  --project-ref sluoxufnqwusmtckklnv
supabase functions deploy stripe-webhook --no-verify-jwt \
  --project-ref sluoxufnqwusmtckklnv
```

(If your CLI version rejects multiple positional slugs, deploy each
function separately: `supabase functions deploy <name> --project-ref …`.)

`supabase/config.toml` already pins `verify_jwt` per function
(`create-checkout-session`/`create-portal-link` → `true`,
`stripe-webhook` → `false`) — the `--no-verify-jwt` flag on the webhook
deploy is belt-and-suspenders for CLI versions that don't read
`config.toml` for this setting; the other two functions rely on
`config.toml`'s `true` and need no flag. **Never** deploy
`create-checkout-session`/`create-portal-link` with `--no-verify-jwt` — the
JWT-claims read in `_shared/claims.ts` assumes the runtime already verified
the signature (see the comment there); skipping verification turns it into
a spoofable auth bypass.

## Step 4 — Register the Stripe webhook

Stripe dashboard → Developers → Webhooks → Add endpoint (do this once per
mode — test and live have separate webhook registrations and separate
signing secrets):

- **URL:** `https://sluoxufnqwusmtckklnv.supabase.co/functions/v1/stripe-webhook`
- **Events to send** (matches the `case` list in
  `supabase/functions/stripe-webhook/sync.ts`):
  - `product.created`, `product.updated`, `product.deleted`
  - `price.created`, `price.updated`, `price.deleted`
  - `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`
  - `checkout.session.completed`

Copy the endpoint's **Signing secret** (`whsec_...`) and re-run Step 2's
`supabase secrets set STRIPE_WEBHOOK_SIGNING_SECRET=...` with the real
value.

## Step 5 — Local dev: forward webhooks

For local iteration against `supabase functions serve` (or a local
`supabase start` stack), forward live Stripe test-mode events to your
machine instead of registering a public endpoint:

```bash
stripe listen --forward-to http://localhost:54321/functions/v1/stripe-webhook
```

`stripe listen` prints its own `whsec_...` — use that (not the Step 4
dashboard secret) in your local `.env`/`--env-file` for
`STRIPE_WEBHOOK_SIGNING_SECRET` while iterating locally; switch back to the
dashboard-issued secret before deploying.

## Step 6 — Deploy `apps/site` to its own Vercel project

`apps/site` (the Astro marketing site, package name `aeronta-site`) is a
**separate app from `apps/web`** and must be a **separate Vercel project** —
do not reuse the `aeronta-inventory` project or deploy from the repo root.
`.claude/memory/lessons.md`'s "Vercel Git auto-deploy from a monorepo root
silently 404s prod on every push" (2026-07-22) documents exactly this
failure mode for `apps/web`: a build that runs from the monorepo root
instead of the app subdirectory produces an empty deployment (`Builds: .
[0ms]`) that can hijack a production alias.

**Deploy prebuilt — do not use plain `vercel deploy --prod`.** This
supersedes the plain `vercel deploy --prod` this runbook (and pre-C4
sessions) used for `apps/web` before C4 — that command still works for
single-app-directory deploys with no cross-directory imports, but no longer
does here. As of C4, both `apps/site` and `apps/web` import
`../../packages/tailwind-preset/*`
(a path outside the app directory, into the shared monorepo package). A
plain `vercel deploy` run from inside `apps/site`/`apps/web` uploads **only
the current working directory** to Vercel's remote build — the remote build
then fails on the missing `packages/tailwind-preset` module (it was never
uploaded). `vercel build --prod` runs LOCALLY, from this checkout, where
the monorepo-relative import resolves fine; `vercel deploy --prebuilt --prod`
then uploads only the already-built `.vercel/output` — no remote build, so
the missing-module failure mode never triggers. This is on top of, not
instead of, the root-build 404 lesson above (never root-built still holds
— always `cd` into the app directory first):

```bash
cd apps/site
vercel link          # first time: create/select a NEW project, e.g. "aeronta-site" —
                      # do NOT select the existing "aeronta-inventory" project
vercel env add PUBLIC_SUPABASE_URL production
vercel env add PUBLIC_SUPABASE_ANON_KEY production
vercel env add PUBLIC_APP_URL production     # https://aeronta-inventory.vercel.app — pricing/CTA links target this
vercel env add PUBLIC_SITE_URL production    # the site's own public URL (astro.config.mjs's `site`, used for sitemap/canonical URLs)
vercel env add PUBLIC_CONTACT_EMAIL production  # OPTIONAL — a real monitored inbox; when unset, the contact form's error fallback shows no email address (safe default)
vercel build --prod
vercel deploy --prebuilt --prod
```

If a GitHub auto-deploy integration is connected for this project later,
set its **Root Directory to `apps/site`** in the Vercel dashboard *first* —
otherwise every push to `main` triggers a root-directory build that 404s
the live site, same as the `apps/web` incident. Prefer CLI-only deploys
(`vercel git disconnect`) unless Root Directory is confirmed correct.

## Step 7 — Deploy `apps/web` (C4 billing UI)

`apps/web` ships new C4 code this rollout (the `/signup` wizard, `/billing`
page, subscription banners, over-quota upgrade CTA) that hasn't gone live
yet — deploy it, using the same prebuilt method as Step 6 and for the same
reason (`apps/web` also now imports `../../packages/tailwind-preset/*`):

```bash
cd apps/web
vercel build --prod
vercel deploy --prebuilt --prod
```

No new `apps/web` env is needed for C4: `VITE_SUPABASE_URL` /
`VITE_SUPABASE_ANON_KEY` are already set on the `aeronta-inventory` Vercel
project (from C2 Task 9's auth activation) — the billing UI calls the Edge
Functions directly against that same Supabase project
(`src/lib/api/billing.ts`'s `functionsBaseUrl()`), and reads
`GET /v1/tenants/{tenant}/billing` through the existing same-origin `/v1`
rewrite to the Railway BFF. Confirm both vars are still set
(`vercel env ls production` from `apps/web`) before relying on this.

## Step 8 — Run the smoke

```bash
AERONTA_SMOKE_EMAIL=<owner-email> \
AERONTA_SMOKE_PASSWORD=<owner-password> \
AERONTA_ANON_KEY=<anon-key> \
AERONTA_BFF_URL=https://bff-production-6568.up.railway.app \
AERONTA_SMOKE_INGEST=1 \
AERONTA_SMOKE_BILLING=1 \
uv run --extra bff python ../../deploy/aeronta_smoke.py   # from services/agent-spine
```

(Any Python interpreter with `httpx` on the path works —
`services/agent-spine/.venv/bin/python deploy/aeronta_smoke.py` from the
repo root is equivalent.) `AERONTA_SMOKE_INGEST`/`AERONTA_SMOKE_BILLING`
are independent — set either, both, or neither. The billing stage only
proves the **read path** (`GET .../billing` returns a 200 with the expected
shape); it does not drive Stripe Checkout — that's the manual checklist
below.

---

## Known limitation — manual tenant activation (until C5 multi-tenant serving)

A fresh self-serve signup completes billing correctly (org created, checkout
paid, webhook synced `tenants.plan_tier`/`subscription_status`) but the
customer **cannot reach the product yet** — two single-tenant assumptions
elsewhere in the stack block them:

- **`apps/web`'s tenant slug map is build-time, not dynamic.** The UI
  resolves a JWT's `tenant_id` claim to a display slug via
  `VITE_TENANT_SLUGS` (a `uuid:slug` map baked in at `vercel build` time,
  read by `apps/web/src/lib/auth/supabase.ts`'s `tenantSlugByUuid`). A brand
  new tenant's uuid isn't in that map, so the app falls into the "no tenant
  access" branch (`App.tsx`) even though the JWT carries valid
  `tenant_id`/`tenant_role` claims.
- **The BFF serves exactly one tenant.** `PLANNER_TENANT` (`bff/asgi.py`)
  is a single env var resolved to one tenant uuid at BFF boot — there is no
  per-request tenant routing to a different backing store for a second
  tenant.

**Until C5 lands per-tenant serving, activate each new signup by hand:**

1. Look up the new tenant's uuid (`select id from tenants where slug =
   '<new-slug>';`).
2. Add `<uuid>:<slug>` to `apps/web`'s `VITE_TENANT_SLUGS` Vercel env var
   (comma-separated with existing entries) and redeploy `apps/web` (Step 7's
   prebuilt method).
3. The BFF constraint is structural, not env-fixable: a genuinely new
   tenant's planner data (recommendations, feature store, etc.) needs either
   a dedicated BFF instance pointed at that tenant (`PLANNER_TENANT=<slug>`)
   or the C5 multi-tenant-serving work — billing/webhook/quota are correct
   for the new tenant regardless, but the Workbench/Overview/etc. won't show
   its data until one of those exists.

## Live signup checklist (manual, run once per environment cutover)

The full signup → Stripe Checkout → webhook → `tenants.plan_tier` chain
needs live Stripe test-mode fixtures and a real email inbox, so it isn't
automated by the Playwright e2e (`apps/web/e2e/signup-billing.spec.ts`,
which is route-mocked) or the smoke stage above (which only reads). Walk it
by hand before calling a cutover done:

- [ ] From the `apps/site` pricing page, click a plan CTA → lands on
      `https://aeronta-inventory.vercel.app/#/signup?plan=<tier>`.
- [ ] **Account step** (`supabase.auth.signUp`, no `emailRedirectTo`
      override — see `SignupWizard.tsx`): submit email/password, receive
      the confirmation email.
      - [ ] **HashRouter caveat — verify this explicitly.** `apps/web`
        routes everything under `/#/...` (`App.tsx`'s `HashRouter`), but
        Supabase appends its confirmation params (`?code=...` /
        `?token_hash=...`) to the URL's **query string**, before any `#`.
        Whether the confirmation link actually lands the user in a live
        session depends on the Supabase project's Authentication → URL
        Configuration → **Site URL** / **Redirect URLs**, not on app code.
        Confirm the Site URL is exactly `https://aeronta-inventory.vercel.app`
        (and that URL is in the allow-list), click a real confirmation
        email, and confirm the wizard's "confirm" step (`continueAfterConfirm`)
        actually finds a session afterward rather than looping. If it
        doesn't, adjust the redirect/allow-list config, not the app.
- [ ] **Org step**: name the organization → confirm
      `create_tenant_for_current_user` succeeds and `tenant_id`/
      `tenant_role` claims appear after `refreshSession()` (the wizard
      moves to the plan step only once they do).
- [ ] **Plan step**: pick an interval → Stripe Checkout (test mode) → pay
      with a test card (`4242 4242 4242 4242`, any future expiry/CVC) →
      redirected to `.../#/billing?checkout=success`.
- [ ] Confirm the webhook applied: `GET .../v1/tenants/{tenant}/billing`
      (or re-run Step 8 with `AERONTA_SMOKE_BILLING=1`) shows the expected
      `plan_tier`/`subscription_status`/`key_quota` — this is the signal
      that `checkout.session.completed` → `customer.subscription.*` →
      `sync.ts` actually ran, not just that Checkout redirected.
- [ ] On `/billing`, confirm "Manage billing" opens the Stripe customer
      portal, and that canceling there flips the app to the read-only
      state (the `role="alert"` "read-only... reactivate" banner on
      `/billing`, and the guardrail blocking writeback tenant-wide).
