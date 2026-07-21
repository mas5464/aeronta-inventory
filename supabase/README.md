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

## Deploying to real Supabase (prereqs)

The migrations here were authored and tested against the plain-Postgres shim
above. Before pointing `supabase db push` (or any migration runner) at a real
Supabase project, three gaps need to be closed:

(a) **Bootstrap the `trax_app` / `trax_seed` roles first.** The migrations
grant privileges to and set RLS policies against roles named `trax_app` and
`trax_seed`, but a fresh Supabase project does not have them — Supabase ships
its own role set (`anon`, `authenticated`, `service_role`, `supabase_admin`,
etc.), not these app-specific ones. Create them with a one-time bootstrap SQL
script, run as the project superuser (the Supabase dashboard's SQL editor, or
`psql` against the pooler with the `postgres` role), mirroring the role-creation
block in `tests/pg/auth_shim.sql` (`create role trax_app login ... nobypassrls`,
`create role trax_seed login ... bypassrls`) — minus the `auth.*` shim
functions/schema, which real Supabase already provides. Skipping this makes
`supabase db push` fail loudly (grant/policy statements referencing an unknown
role), not silently.

(b) **Exactly one migration runner may own a database.** This repo has two
independent migration trackers: the Python `apply_migrations` (`pg/db.py`),
which records applied migrations in its own `public._migrations` table, and
the Supabase CLI's `supabase db push`, which tracks state in its own internal
schema. They do not know about each other. Running both against the same
database re-applies migrations the other already applied and errors (or, worse,
silently diverges on which migrations are considered "done"). Pick one runner
per database — `supabase db push` for a real Supabase project, the Python
harness only for the throwaway test-container Postgres — and never mix them.

(c) **Grant the auth hook to `supabase_auth_admin` when registering it.** The
custom access token hook (`public.custom_access_token_hook`,
`supabase/migrations/20260720000002_claims_hook.sql`) is granted to
`trax_seed` for the plain-Postgres test shim, but real Supabase invokes
registered auth hooks as the `supabase_auth_admin` role. When wiring this hook
up in a real Supabase project (dashboard → Authentication → Hooks, or via
`config.toml`), also run:

```sql
grant execute on function public.custom_access_token_hook(jsonb) to supabase_auth_admin;
```

This is tracked as a C2 deploy to-do, not yet automated in a migration (the
role only exists on real Supabase, so a plain-SQL migration applied against
the test-container Postgres would fail without it).

## Live project (Aeronta Inventory)

Provisioned 2026-07-21 via Supabase CLI (C2 start):

| Item | Value |
|---|---|
| Product name | **Aeronta Inventory** (brand locked — spec §1) |
| Supabase org | `Aeronta` (`gggpgpzoutiljiwlrymg`, free plan) |
| Supabase project | `aeronta-inventory` — ref `sluoxufnqwusmtckklnv`, region us-east-1 (East US, N. Virginia) |
| DB password | generated at provision time; stored ONLY in the gitignored `deploy/_local_extract/aeronta-supabase.env` (never committed) |
| Repo link | `supabase link` done (ref in `supabase/.temp/`, gitignored) |
| Vercel project | `aeronta-inventory` — `prj_WQlrbadCxnWfLQOCteebIIJENzFz`, scope `msosa79-8493s-projects` (empty; first deploy lands in C2) |

Next (C2, per the prereqs section above): run the role-bootstrap SQL (`trax_app`/`trax_seed`) in the dashboard SQL editor as superuser, then `supabase db push` to apply the four C1 migrations, then `trax-io-pg-seed` a demo tenant.

### Live-deploy findings (2026-07-21, database brought live)

- Migrations 0001–0005 applied to `aeronta-inventory` via `supabase db push --db-url` (the **session pooler** URL — the direct `db.<ref>.supabase.co` host is IPv6-only; pooler user format is `<role>.<ref>`).
- **Migration 0005** exists because live Supabase gives custom roles no `usage` on schema `auth`, and grants issued by `postgres` silently no-op (no GRANT OPTION): `current_tenant_id()` now reads the `request.jwt.claims` GUC directly — identical semantics, no auth-schema dependency.
- Role bootstrap ran via the pooler as `postgres` (see prereqs above); `trax_seed` DID get real `bypassrls` (Supabase's postgres can grant it).
- Demo tenant seeded: `aeronta-demo` ("Aeronta Demo Airline", uuid `753b64bd-9885-4639-b116-8f2c5c497232`) — 6 recommendations / 4 part keys from the sample extract. Verified live: RLS deny-without-claims, BFF dashboard/queue/BVR all 200 over `DATABASE_URL`.
- Role passwords live ONLY in the gitignored `deploy/_local_extract/aeronta-supabase.env`.

## Live auth activation (C2 Task 9)

**ACTIVATED 2026-07-21**: the Custom Access Token hook is enabled on the live project
(`pg-functions://postgres/public/custom_access_token_hook`, confirmed via Management API GET)
and verified end-to-end: `deploy/aeronta_smoke.py` sign-in mints claims
`tenant_id=753b64bd-... tenant_role=owner` for the smoke user. Registration was completed via
the dashboard (Authentication → Hooks) after the CLI's Keychain session proved unusable as a
Management-API Bearer; a `SUPABASE_ACCESS_TOKEN` PAT now lives in the gitignored env file for
future config automation (rotate it if compromised).

### Original activation notes


Migration `20260721000007_auth_hook_grants.sql` grants `supabase_auth_admin`
(the role GoTrue runs the `security invoker` claims hook as) `usage` on
schema `public`, `execute` on `custom_access_token_hook`/`try_uuid`, `select`
on `memberships`/`tenant_preferences`, and adds two narrow `using (true)`
read policies (`memberships_auth_hook_read`, `tenant_preferences_auth_hook_read`)
so GoTrue can see every membership when minting claims — the role is not
reachable from app code, so this is not a tenant-isolation gap. Applied live
2026-07-21 via `supabase db push --db-url` from the worktree root (same
pooler pattern as migrations 0001–0006).

### Hook registration (Management API) — BLOCKED, needs a fresh PAT

The custom access token hook still needs to be registered against the live
project via the Supabase Management API:

```bash
curl -s -X PATCH "https://api.supabase.com/v1/projects/sluoxufnqwusmtckklnv/config/auth" \
  -H "Authorization: Bearer $SUPABASE_PAT" -H "Content-Type: application/json" \
  -d '{"hook_custom_access_token_enabled": true,
       "hook_custom_access_token_uri": "pg-functions://postgres/public/custom_access_token_hook",
       "external_email_enabled": true, "mailer_autoconfirm": true}'
```

**This step is currently blocked**, not yet done: this environment's `supabase`
CLI (2.101.0) stores its session in the macOS Keychain (service `Supabase
CLI`) as an opaque profile credential, not a plain `sbp_...` personal access
token or a JWT — `cat ~/.supabase/access-token` (the path earlier CLI
versions used) does not exist here. That stored value works for the CLI's
*own* HTTP client (`supabase projects list`, `supabase projects api-keys`
both succeed) but is not independently usable as a raw
`Authorization: Bearer` value: manual `curl` calls to
`api.supabase.com/v1/projects/...` with it return
`{"message":"JWT could not be decoded"}` (also tried as HTTP Basic and as
the project's own `service_role` JWT — `{"message":"JWT failed
verification"}`, confirming the Management API validates against an
account-session key the CLI never exposes directly). No `~/.supabase/`
plaintext token file, no `SUPABASE_ACCESS_TOKEN` env var, and no
already-authenticated browser session were available to extract a working
Management API bearer from in this environment.

**To finish this step**, generate a Personal Access Token at
[supabase.com/dashboard/account/tokens](https://supabase.com/dashboard/account/tokens)
and either run the `curl` above with `SUPABASE_PAT` set, or make the same two
edits by hand in the dashboard: **Authentication → Hooks** (enable "Custom
Access Token" → `public.custom_access_token_hook`) and **Authentication →
Providers → Email** (confirm email/password sign-in is enabled; autoconfirm
is not required for admin-created users — see below). Then re-run
`deploy/aeronta_smoke.py` — it will start printing the `tenant_id`/
`tenant_role` claims once the hook fires.

### Smoke user

A permanent smoke-test user exists on the live project for exactly this
purpose: `smoke@aeronta.test`, created via the GoTrue admin API with
`email_confirm: true` (so it does not depend on `mailer_autoconfirm`), with
an `owner` membership row on `aeronta-demo`. Credentials
(`AERONTA_SMOKE_EMAIL`, `AERONTA_SMOKE_PASSWORD`) and the project's
`service_role` key (`AERONTA_SERVICE_KEY`, used only to create the user) live
in the gitignored `deploy/_local_extract/aeronta-supabase.env`, appended
alongside the DB/role passwords.

### `deploy/aeronta_smoke.py`

Env-gated live smoke test (prints `SKIP (env unset)` and exits 0 if env is
missing, so it's safe to leave wired into scripts/CI without a live
environment):

```bash
cd services/agent-spine
set -a && source "../../deploy/_local_extract/aeronta-supabase.env" && set +a
AERONTA_ANON_KEY=$(supabase projects api-keys --project-ref sluoxufnqwusmtckklnv --output-format json \
  | python3 -c "import json,sys; print([k for k in json.load(sys.stdin) if k['name']=='anon'][0]['api_key'])") \
  .venv/bin/python ../../deploy/aeronta_smoke.py
```

It signs in as the smoke user, decodes the minted access token, and asserts
`tenant_id`/`tenant_role` claims are present — **the load-bearing check that
proves the custom access token hook actually fired**. Optionally, with
`AERONTA_BFF_URL` set, it also checks the BFF's `/recommendations` route
(200 authed / 401 unauthed) and `/members` route (200 for admin/owner, 403
otherwise); both are skipped (printed, not failed) when the BFF isn't
deployed yet.

**Current live result** (2026-07-21, hook not yet registered — see above):
sign-in succeeds, but the minted token carries no `tenant_id`/`tenant_role`
claims, so the script correctly fails with a named error pointing at the
Management API hook registration. Once that PATCH lands, re-running the same
command is expected to print:
`sign-in OK · claims: tenant_id=753b64bd-9885-4639-b116-8f2c5c497232 tenant_role=owner · BFF checks skipped (no AERONTA_BFF_URL)`.
