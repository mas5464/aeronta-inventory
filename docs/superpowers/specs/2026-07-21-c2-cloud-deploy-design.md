# C2 — Cloud Deploy: Railway BFF + Worker, apps/web on Vercel, Supabase Auth Shell

**Date:** 2026-07-21
**Status:** Approved (design walkthrough complete)
**Owner:** Miguel Sosa
**Parent spec:** [2026-07-20-commercialization-architecture-design.md](2026-07-20-commercialization-architecture-design.md) (§10 row C2)
**Live foundation this builds on:** Supabase project `aeronta-inventory` (ref `sluoxufnqwusmtckklnv`, us-east-1) with migrations 0001–0005 applied, roles `trax_app`/`trax_seed` bootstrapped, demo tenant `aeronta-demo` seeded (uuid `753b64bd-9885-4639-b116-8f2c5c497232`); Vercel project `aeronta-inventory` (`prj_WQlrbadCxnWfLQOCteebIIJENzFz`, empty); connectivity is pooler-only (`<role>.<ref>@aws-0-us-east-1.pooler.supabase.com:5432` — direct host is IPv6-only). Secrets live in the gitignored `deploy/_local_extract/aeronta-supabase.env`.

## 1. Decisions locked (user-approved)

| Decision | Choice |
|---|---|
| Scope | **BFF + idle worker** on Railway (worker polls the new `jobs` table; C3 gives it real work) |
| Auth shell depth | **Full user management** — login/logout, members list, invites, role changes, tenant switcher |
| Sign-in methods | **Email/password only** at C2 (OAuth/SAML later via dashboard + per-customer setup) |
| Auth architecture | **Approach A** — supabase-js in the frontend; JWT verified at the BFF; per-request tenant impersonation as `trax_app`. Rejected: cookie-mediated BFF auth (hand-rolls what supabase-js provides), Next.js wrapper (umbrella-rejected rewrite) |
| Deploy mechanics | CLI-driven (`railway up`, `vercel deploy`); CI automation deferred. Default `*.railway.app`/`*.vercel.app` domains until the brand domain is purchased. Production environment only |
| Prereq (user, one-time) | Railway CLI install + `railway login` (interactive browser auth) — the CLI is not currently installed |

## 2. Deployment topology

```
  user ──► Vercel (aeronta-inventory, static Vite build of apps/web)
              │  /v1/* rewrite (same-origin, no CORS)
              ▼
           Railway "bff"    ── DATABASE_URL (trax_app via pooler) ──► Supabase
           Railway "worker" ── (trax_seed via pooler; idle jobs poll) ──► aeronta-inventory
```

- **Vercel:** `apps/web` built with `VITE_SUPABASE_URL` + anon key (public by design) baked in; `vercel.json` rewrites `/v1/(.*)` to the Railway BFF public URL. Deployed into the existing project.
- **Railway:** one project (`aeronta`), two services from the existing `deploy/bff.Dockerfile` image: `bff` (uvicorn `trax_io_spine.bff.asgi:app`, public domain, health check `/v1/tenants/{tenant}/killswitch`) and `worker` (same image, command `python -m trax_io_spine.pg.worker`). All secrets (pooler DSNs, Supabase service key, JWT verification secret) live only in Railway variables — never in the repo or image.

## 3. Auth — JWT verification at the BFF

- **New `bff/auth.py` middleware:** validates `Authorization: Bearer <supabase JWT>` using the project's JWT verification material from env (`AUTH_JWT_SECRET` for HS256 or JWKS URL — whichever the project uses; discovered at implementation from the live project settings). Missing/invalid/expired → 401. Verified claims yield `tenant_id` (uuid), `tenant_role`, `sub`; the route's `{tenant}` path param must resolve to the same tenant or 403. Downstream store calls use the existing `tenant_conn` impersonation with the VERIFIED claims — the BFF pool runs as `trax_app`.
- **Migration 0006 (part 1):** `security definer` function `public.resolve_tenant_slug(slug text) returns uuid` (owner `postgres`, execute granted to `trax_app`) so boot + per-request slug→uuid resolution works as `trax_app` — retiring the documented bypassrls-role boot workaround from C1/Task 13.
- **Claims-hook activation on live Supabase:** `grant execute on function public.custom_access_token_hook(jsonb) to supabase_auth_admin` (+ `grant usage on schema public` if missing for that role) and enable the Custom Access Token hook in auth config, so real logins mint `tenant_id`/`tenant_role` claims. Tenant switching re-mints with a requested `tenant_id` (C1's hook validates membership; foreign requests fall back — already tested).
- **Dev-mode fallback (explicit, loud):** when `AUTH_JWT_SECRET`/JWKS env is absent, the middleware passes through in the current trusted-path-param mode and logs a prominent warning at boot. Local Docker compose and the existing test suite run unchanged; the live deployment always sets the secret.

## 4. Full user management

- **BFF routes** (all require verified role `admin` or `owner` from claims; `owner` required to grant/revoke `owner`):
  - `GET /v1/tenants/{t}/members` — memberships joined with email/last-sign-in via the Supabase Admin API (service key, BFF-side only)
  - `POST /v1/tenants/{t}/members/invite` — `inviteUserByEmail` + membership row `(user_id, tenant, role)`
  - `PATCH /v1/tenants/{t}/members/{user_id}` — role change
  - `DELETE /v1/tenants/{t}/members/{user_id}` — remove membership; refuses to remove the last `owner` (409)
- **Migration 0006 (part 2):** admin-scoped write policies on `memberships` for `trax_app` (`insert`/`update`/`delete` gated on claims `tenant_role in ('admin','owner')` and `tenant_id` match) + the matching grants — C1 left memberships read-only by design; this is the deliberate widening, still RLS-fenced per tenant.
- **apps/web:** login/logout screens (supabase-js, email/password), session-aware API client (JWT attached to every `/v1` call; 401 → redirect to login), a **Members** settings page (list, invite, change role, remove — UI gated by the JWT's role), and a tenant switcher for multi-membership users (re-mint + reload). The `Metric`/`ProvChip` provenance invariant is untouched — auth surfaces are chrome, not operational numbers.

## 5. Worker + jobs table

- **Migration 0006 (part 3):** `jobs` table per the umbrella spec — `id identity, tenant_id uuid fk, kind text check in ('ingest','recompute','bvr'), status text check in ('queued','running','done','failed','dead') default 'queued', payload jsonb, attempts int default 0, claimed_at/finished_at timestamptz, error text` — RLS'd (tenant-scoped select for `trax_app`; insert for `trax_app`; claim/update via worker's `trax_seed`), claim query `for update skip locked`.
- **`trax_io_spine.pg.worker`:** poll loop (configurable interval, default 5s), claims one job, dispatches by `kind` from a handler registry (empty in C2 → immediate `dead` with error "no handler registered"; C3 registers real handlers), retry ×3 → `failed`/`dead`, heartbeat log line per cycle. Graceful SIGTERM shutdown (Railway restarts).

## 6. Pre-flight hardening (from C1's final review — folded into C2)

1. Expression index `writeback_ledger (tenant_id, (entry->>'idempotency_key'))` (migration 0006, part 4) — `_replay` stops scanning per write.
2. `bvr()` computes + writes its cache in ONE transaction (closes the multi-worker stale-serve window found in the final review).
3. `pg` extra split: `pg = [psycopg]` (deploy image) / `pg-test = [psycopg, testcontainers]` (test harness) — testcontainers leaves the production image.
4. One-migration-runner rule: the live database is owned by `supabase db push` exclusively (already documented; C2 adds nothing via the Python runner against live).

Deliberately NOT in C2: the 59K scale-gate run (still blocked on regenerating a full-network snapshot from a real extract), Stripe/billing (C4), upload intake + real job handlers (C3), custom domain, CI pipeline, SAML.

## 7. Error handling

- 401 (no/invalid token) and 403 (tenant mismatch, insufficient role) are distinct and tested; kill-switch 423 semantics unchanged.
- Members routes surface Supabase Admin API failures as 502 with a safe message (no admin-API details leaked); invite idempotency: inviting an existing member returns 409.
- Worker: unknown kind → `dead` immediately; handler exception → retry ×3 with backoff then `failed`; every transition writes `error` text. Nothing crashes the loop.
- BFF boot fails loudly if `DATABASE_URL` is set but the tenant registry is unreachable (existing behavior preserved).

## 8. Testing

- **Unit/harness (CI-runnable, testcontainers):** JWT middleware (valid/expired/bad-signature/missing, tenant mismatch 403, role gates, dev-mode fallback warning), members routes end-to-end on the pg harness with a faked Admin API client (protocol seam, same pattern as `fake_emro`), jobs claim semantics (skip-locked, retry, dead-letter), migration 0006 isolation tests for `jobs` + the new memberships write policies (two-tenant convention).
- **Live smoke (env-gated, like the eMRO smoke pattern):** `AERONTA_SMOKE_*` env → script signs in a real test user against live Supabase, calls the deployed BFF through the Vercel rewrite, asserts: queue 200 with token, 401 without, members list 200 as owner, 403 as planner. Skips clean when env unset.
- **Frontend:** Vitest for login flow, authed client (JWT attach + 401 redirect), Members page (role-gated rendering, invite/remove flows against a mocked client). Existing 288+ tests stay green.

## 9. Rollout order (the plan's task spine)

1. Migration 0006 (slug-resolve + memberships write policies + jobs + idempotency index) — pushed to live
2. Hardening: bvr single-txn cache; extra split
3. BFF auth middleware + members routes (+ tests)
4. Worker module (+ tests)
5. Claims-hook activation + email/password config on live Supabase; test user + smoke script
6. Railway: CLI login (user step) → project + two services + variables → deploy → verify live
7. apps/web auth shell (login, client, members page, switcher) + Vitest
8. Vercel: build + rewrite config → deploy → end-to-end smoke through the rewrite
9. Bookkeeping (ROADMAP C2 row, TASKS, CLAUDE.md env/commands, parent-spec C2 row)

## 10. Risks

| Risk | Mitigation |
|---|---|
| Supabase JWT verification material mismatch (legacy HS256 secret vs new JWKS) | Implementation step reads the live project's auth settings first; middleware supports both paths; smoke test proves the real token verifies |
| Claims hook not firing on live logins (registration misconfig) | Smoke script asserts the minted JWT actually carries `tenant_id`/`tenant_role` before anything else depends on it |
| Admin API service key blast radius | Key only in Railway env for the `bff` service; never in the frontend, image, or repo; members routes are the only consumers |
| Railway egress/pooler limits surprise | The C1 finding stands: pooler-only connectivity; Railway→Supabase over the session pooler is the tested path; Fly.io remains the named fallback |
| Idle worker cost | Single small service; acceptable per the BFF+worker decision; C3 gives it work |
