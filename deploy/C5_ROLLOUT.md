# C5 Rollout Runbook — Multi-Tenant Serving + Scheduled Recompute

Controller-executed steps to take the C5 (multi-tenant BFF serving +
pg_cron-scheduled recompute) build from "code merged" to "live." This
closes the exact gap C4's runbook flagged as a known limitation: until now,
a paying self-serve signup completed billing but could not reach the
product without a human editing `VITE_TENANT_SLUGS` and possibly
dedicating a BFF instance to the new tenant. C5 removes both walls. This
runbook's Step 7 is the proof.

Live facts this runbook assumes (see [supabase/README.md](../supabase/README.md)
for the full table, and `deploy/C4_ROLLOUT.md`'s own live-facts table):

| | |
|---|---|
| Supabase project | `aeronta-inventory`, ref `sluoxufnqwusmtckklnv` |
| Pooler host | `aws-0-us-east-1.pooler.supabase.com:5432` — **use `aws-0`, verified live; `aws-1` does not resolve** (see `.claude/memory/lessons.md` / the C4 runbook fix commit `0c94c7e`) |
| BFF (Railway) | `https://bff-production-6568.up.railway.app` |
| `apps/web` (Vercel) | `https://aeronta-inventory.vercel.app` (production, `/v1/*` rewrite → the Railway BFF) |
| pg_cron | verified available on this project: **1.6.4, `installed_version` is `None`** (not yet created) |
| Secrets | `deploy/_local_extract/aeronta-supabase.env` (gitignored) — holds `AERONTA_SUPABASE_DB_PASSWORD` plus the smoke-user credentials referenced below |

Run every `supabase` CLI command below with `--project-ref sluoxufnqwusmtckklnv`
(or `supabase link` once, up front, so it's implicit) — the `db push`
command below uses `--db-url` directly instead, which needs no link.

---

## Prerequisite — confirm C4 is actually live before starting

**Do not assume this.** This repo's own bookkeeping disagrees with itself
on the point as this is written: `ROADMAP.md`/`TASKS.md` mark C4
"CODE COMPLETE 2026-07-23 ... **live rollout pending**", while this
sub-project's own design doc (and the SDD task brief this runbook was
written from) assume migrations 0001–0012 are already live. **Check
directly rather than trust either document** — the check is cheap and this
runbook depends on the answer:

```bash
supabase migration list \
  --db-url "postgresql://postgres.sluoxufnqwusmtckklnv:<DB_PASSWORD>@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
```

(`<DB_PASSWORD>` = `AERONTA_SUPABASE_DB_PASSWORD` in the gitignored
`deploy/_local_extract/aeronta-supabase.env`.) Look at the `Remote` column
for `20260723000010_billing_tenants`, `…011_billing_stripe_mirror`,
`…012_billing_leads_and_org_rpc`:

- **All three already show a remote timestamp** → C4 is live. Continue to
  Step 1 below; `supabase db push` there only has 0013–0014 left to apply.
- **Any of the three is missing remotely** → C4 is NOT live yet. Stop here
  and run [`deploy/C4_ROLLOUT.md`](C4_ROLLOUT.md) in full first (Stripe
  products/prices, secrets, its own `db push`, Edge Function deploys,
  webhook registration, grandfathering `aeronta-demo`, the C4 BFF/worker
  redeploy). This runbook is not a substitute for it — Step 7's acceptance
  gate below drives a real Stripe checkout, and migration 0014's
  eligibility query reads `tenants.subscription_status`, a column
  migration 0010 adds; neither works without C4 live.

Either way, `supabase db push` (Step 1) is safe to **run** as-is: it applies
whatever is pending, in order, and skips whatever's already applied — it
cannot double-apply 0010–0012 if C4 already pushed them.

**"Safe to run" is not the same as "safe to proceed past this point,"
though.** If C4 is NOT already live and you push anyway, Step 1 newly
applies migration 0010's `tenants.subscription_status` column — nullable,
no default — so it lands `NULL` for every existing row, including the live
demo tenant `aeronta-demo`. `NULL` is not one of
`{trialing, active, past_due}`, so once Step 2 redeploys the BFF, C4's
`AuthMiddleware` 402 write-gate (`_ACTIVE_SUBSCRIPTION_STATUSES`) locks
`aeronta-demo` read-only on every write. `deploy/C4_ROLLOUT.md`'s
**Step 2.6 — Grandfather the live `aeronta-demo` tenant** exists
specifically to close this gap (a direct SQL update setting
`subscription_status = 'active'` for that tenant, run BEFORE its BFF
redeploy). So: only continue past this Prerequisite section once C4 is
already live AND `aeronta-demo` is already grandfathered — or, if you are
running C4's rollout and this one back-to-back, run C4_ROLLOUT.md's
Step 2.6 (or its direct SQL equivalent) before this runbook's Step 2.

---

## Step 1 — Apply the C5 migrations (0013–0014)

```bash
supabase db push \
  --db-url "postgresql://postgres.sluoxufnqwusmtckklnv:<DB_PASSWORD>@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
```

Verify both new functions exist, and — since migration 0014's
`enqueue_due_recomputes()` reads `tenants.subscription_status` — that the
column it depends on (migration 0010) is really there:

```sql
select proname from pg_proc
 where proname in ('tenants_for_current_user', 'enqueue_due_recomputes');
-- expect 2 rows

select column_name from information_schema.columns
 where table_schema = 'public' and table_name = 'tenants'
   and column_name = 'subscription_status';
-- expect 1 row
```

## Step 1.5 — Verify `auth.jwt()` resolves inside `tenants_for_current_user()` on the real project

**Verify this live, don't assume it.** `tenants_for_current_user()`
(migration 0013) is `security definer` and reads `auth.jwt()->>'sub'`
directly. The local Postgres test harness's `auth_shim.sql` grants custom
roles broad access to a hand-built `auth` schema; real Supabase does not
grant custom roles `usage` on the real `auth` schema the same way — this is
exactly the gap migration 0005 was written to route around for
`current_tenant_id()` (see [supabase/README.md](../supabase/README.md)'s
"Live-deploy findings").

This specific function is new, but the pattern isn't: C4's
`create_tenant_for_current_user` (migration 0012) uses the identical
`security definer` + `(auth.jwt()->>'sub')::uuid` construction, so if C4's
"Live signup checklist" org step has already been walked successfully on
this project, that's real corroborating evidence the mechanism works here.
Still run this check explicitly — it is two minutes, it proves the actual
new function/grant (not just the pattern), and the brief for this runbook
calls it out by name as a "verify live, don't assume" item — but expect it
to pass if C4's org step already has.

Simulate exactly what `bff/asgi.py`'s `_whoami_reader` does per request
(`tenant_conn(..., sub=sub)`, i.e. `set_config('request.jwt.claims', ...)`)
using the permanent smoke user's own id, over the same pooler connection as
Step 1. Use `false` (session-scoped), not `true` (transaction-scoped, what
the BFF itself uses inside one request) — this is a multi-statement psql/
SQL-editor session, not a single transaction, and `true` would silently
stop applying after the first statement's implicit transaction ends:

```sql
-- 1. The smoke user's own id (deploy/aeronta_smoke.py signs in as this user).
select id, email from auth.users where email = 'smoke@aeronta.test';
-- => copy the id printed here into <SMOKE_USER_ID> below

-- 2. Simulate the claims GUC tenant_conn() would set for this user, then
--    call the function exactly as whoami.py's tenants_for() does.
select set_config('request.jwt.claims',
                   json_build_object('sub', '<SMOKE_USER_ID>')::text,
                   false);
select * from public.tenants_for_current_user();
```

**Expect:** one row, `slug = 'aeronta-demo'`, `role = 'owner'`.

**If it returns zero rows instead:** `auth.jwt()` isn't resolving inside
this `security definer` context on the live project the way it does
locally. Apply the same fix `current_tenant_id()` already went through —
rewrite the function body to read
`current_setting('request.jwt.claims', true)::jsonb->>'sub'` directly
instead of `auth.jwt()->>'sub'` (identical semantics, no `auth`-schema
dependency), ship it as a new migration (`20260724000015_...`), and re-run
this check before continuing.

## Step 1.6 — Run the Supabase security advisor (C4 carry-forward, never done)

Carried forward from C4's final review and not yet done for any `db push`
so far. Dashboard → **Database → Advisors** (Security Advisor first, then
Performance):
`https://supabase.com/dashboard/project/sluoxufnqwusmtckklnv/advisors/security`
— or, if operating through an agent session with the Supabase MCP server
connected, the equivalent `get_advisors` tool call (`type: "security"`,
then `type: "performance"`).

Both new functions already defend against the most common finding here
(`set search_path = public` is set explicitly in both migrations 0013 and
0014, so "function search_path mutable" should not fire on them) — this
check is about the **cumulative** state across all of 0001–0014, per the
carry-forward note ("re default-privilege grants"), not specifically about
the two new functions. Triage: any `ERROR`-level finding blocks
proceeding; `WARN`-level findings get recorded (not silently ignored) and
triaged by the controller.

---

## Step 2 — Redeploy the BFF and worker

```bash
railway up -s bff
railway up -s worker
```

Run both from the **repository root** of the checkout holding the C5 code
— `railway up` uploads the current working directory, and
`RAILWAY_DOCKERFILE_PATH` (`deploy/bff.Dockerfile` / `deploy/worker.Dockerfile`)
is a path relative to that upload root, so this must be the repo root, not
a subdirectory (see `CLAUDE.md`'s Railway notes and
`.claude/memory/lessons.md`'s "Railway ignores non-standard config
filenames" entry). **The worker must ship before Step 5 schedules the
cron job** — an unrecognized `recompute` job kind would otherwise
dead-letter against the old worker image.

No env var changes are needed on either service for this step.
`PLANNER_TENANT` is now a pre-warm hint only, not a requirement
(`bff/asgi.py`'s module docstring) — whatever it's currently set to on the
`bff`/`worker` Railway services (set, unset, or pointed at a slug that no
longer resolves) is fine as-is; boot no longer raises on an unresolvable or
missing value, it just skips the pre-warm and serves every tenant via the
registry on first request.

Verify:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://bff-production-6568.up.railway.app/healthz
# expect 200
curl -s -o /dev/null -w '%{http_code}\n' https://bff-production-6568.up.railway.app/v1/auth/whoami
# expect 401 (no Authorization header) — proves the route is live, not proves auth
```

## Step 3 — Redeploy `apps/web` (prebuilt)

```bash
cd apps/web
vercel build --prod
vercel deploy --prebuilt --prod
```

Deploy prebuilt, not plain `vercel deploy --prod` — `apps/web` imports
`../../packages/tailwind-preset/*`, outside the app directory, and a plain
remote build can't see it (see `.claude/memory/lessons.md`'s C4 addendum
and `deploy/C4_ROLLOUT.md` Step 7). This ships the code that reads
`GET /v1/auth/whoami` instead of the build-time tenant map — `useAuth.tsx`
now sources `tenantSlug`/`tenants` exclusively from that endpoint.

## Step 4 — Remove `VITE_TENANT_SLUGS` and redeploy again

The frontend no longer reads this variable (Step 3's build already shipped
that change) — remove it from the Vercel project as cleanup, then redeploy
once more so the build is proven to succeed with it gone entirely, not just
unused:

```bash
cd apps/web
vercel env rm VITE_TENANT_SLUGS production
# confirm the interactive removal prompt
vercel build --prod
vercel deploy --prebuilt --prod
```

Check `vercel env ls production` (and `preview`/`development`, if it was
ever added there) shows no `VITE_TENANT_SLUGS` row left.

Verify: `grep -rn "VITE_TENANT_SLUGS" deploy/` from the repo root — as of
this writing it returns 6 lines, all inside THIS file (the intro, this
step's own three lines, this verify line itself, and Step 7's acceptance
checklist below) and NONE inside `deploy/C4_ROLLOUT.md` or any other file
under `deploy/`. The line count will drift as this file is edited; the
check that actually matters is that `deploy/C4_ROLLOUT.md` contributes zero
hits, confirming its references were retired (Step 2 of this sub-project's
implementation, tracked separately from this rollout).

---

## Step 5 — Enable + schedule pg_cron

As `postgres` over the pooler (same connection as Step 1). Enabling the
extension needs `postgres`; schedule the job with the plain `cron.schedule`
form, which runs the job as whichever role calls it — here, `postgres`.

**Do not call `cron.schedule_in_database(..., 'postgres', 'trax_seed')`**
to try to run the job as `trax_seed` instead of the caller: pg_cron only
allows scheduling a job under a role other than the caller when the caller
is a true superuser, and Supabase's `postgres` role is **not** a superuser
(`rolsuper = false` — only the internal `supabase_admin` role has that). A
`postgres`-called `schedule_in_database(...)` targeting a different
`username` fails outright with `ERROR: must be superuser to create a job
for another role`.

Running the job as `postgres` is not a least-privilege compromise, either.
`enqueue_due_recomputes()` is `security definer`, so its body always
executes with the privileges of its **owner** — `postgres`, since
`postgres` created it via `db push` — no matter which role's connection
invoked it. Ownership also gives `postgres` implicit `EXECUTE` on the
function regardless of migration 0014's `revoke execute ... from public`,
so scheduling the job to run as `postgres` doesn't widen access. The real
access boundary — keeping `authenticated`/`trax_app` from invoking this
function directly (e.g. over PostgREST) — is that same
`revoke`/`grant execute ... to trax_seed` pair in migration 0014, and it is
untouched by which role runs the scheduled job:

```sql
create extension if not exists pg_cron;

select cron.schedule(
  'aeronta-nightly-recompute',
  '0 3 * * *',
  $$select public.enqueue_due_recomputes()$$
);
```

Verify the schedule exists:

```sql
select jobid, schedule, command, active
  from cron.job
 where jobname = 'aeronta-nightly-recompute';
-- expect 1 row: schedule = '0 3 * * *', active = true
```

**To check it actually fired** (any time after the first 03:00 UTC run):

```sql
select status, return_message, start_time, end_time
  from cron.job_run_details
 where jobid = (select jobid from cron.job where jobname = 'aeronta-nightly-recompute')
 order by start_time desc limit 5;
```

**To unschedule it** (if it misbehaves — e.g. enqueues runaway jobs, or
needs to pause while debugging the worker):

```sql
select cron.unschedule('aeronta-nightly-recompute');
```

This only removes the schedule; `public.enqueue_due_recomputes()` itself is
untouched and safe to call manually (see Step 6) whether or not the cron
schedule exists.

**If `create extension pg_cron` is unavailable** (plan-tier restriction,
etc.): `enqueue_due_recomputes()` is the entire seam any scheduler needs to
call — nothing else in C5 is pg_cron-specific. Substitute any mechanism
that can run one SQL statement roughly nightly, connected as a role with
execute on the function (`trax_seed`): a small addition to the existing
worker loop that calls `select public.enqueue_due_recomputes()` once every
~24h, a GitHub Actions scheduled workflow running `psql -c "..."`, or
Railway's own cron plugin hitting the same statement. Nothing else in this
runbook changes.

## Step 6 — Dry-run the enqueue once

```sql
select public.enqueue_due_recomputes();
-- returns the number of tenants enqueued (0 is valid — e.g. if every
-- eligible tenant already has a queued/running job)

select id, tenant_id, kind, status
  from jobs
 where kind = 'recompute'
 order by id desc limit 5;
```

Wait a few seconds (however long the worker's poll interval is), then
re-run the `jobs` query and confirm each row's `status` reached `done` (not
stuck `queued`/`running`, not `failed`, not `dead`). A row that never leaves
`queued` means the worker from Step 2 isn't running or isn't polling; a
`failed` row means read its `result`/error detail before proceeding — this
replays the tenant's last successful ingest payload, so a failure here
indicates a real problem, not something to route around. A `dead` row means
the worker that claimed it has no handler registered for `kind='recompute'`
at all (`worker.run_once`'s `HANDLERS.get(kind) is None` branch) — almost
always a STALE worker image that predates this rollout's Step 2 redeploy,
dead-lettering the job immediately with no retry. Redeploy the worker
(Step 2) and re-run this dry-run.

---

## Step 7 — Acceptance gate: a brand-new tenant, zero manual steps

**This is the point of the whole sub-project.** Prove a signup nobody
touches by hand reaches working recommendations in the app.

Run [`deploy/C4_ROLLOUT.md`](C4_ROLLOUT.md)'s "Live signup checklist"
through the account step, org step, and plan step (Stripe test-mode
checkout, test card `4242 4242 4242 4242`) up to the point the wizard
lands on `.../#/billing?checkout=success`. (If Stripe test-mode products/
webhook aren't registered yet, that's a C4 gap — see the Prerequisite
section above; this runbook assumes C4's Stripe wiring is already live and
only adds the checks below.) Use a clearly test-labeled organization name
(e.g. "C5 Rollout Check") so the resulting tenant is obviously not a real
customer — `create_tenant_for_current_user` slugifies the name and appends
a random 6-character suffix (migration 0012), so record the exact slug it
lands on:

```sql
select slug, name, created_at from tenants order by created_at desc limit 1;
```

Then the checks that are new for C5:

- [ ] Immediately after `checkout=success`, reload the app at `/` (not a
      deep link). Confirm the **Overview page renders** — not the "no
      tenant access" message, not stuck on "Loading your workspace". This
      is `AppShell`'s `tenantStatus === "ready"` gate
      (`apps/web/src/lib/auth/useAuth.tsx`) clearing on its own, driven by
      `GET /v1/auth/whoami`.
- [ ] Confirm **zero manual activation happened**: no `VITE_TENANT_SLUGS`
      edit, no `PLANNER_TENANT` change, no BFF/worker redeploy scoped to
      this tenant, since Steps 2–4 above (which ran before this tenant
      ever existed).
- [ ] On **Data & Connections**, upload the three sample files in
      `deploy/sample_upload/` (`parts.csv`, `stock.csv`,
      `demand_history.csv` — the same tiny canonical batch
      `deploy/aeronta_smoke.py`'s ingest stage uses) through the upload
      panel. Wait for the ingest history entry to reach **done**.
- [ ] On **AI Recommendations** or **Workbench**, confirm recommendation
      rows are visible for this brand-new tenant.
- [ ] Record: tenant slug, organization name, and timestamp, for the
      rollout record.

If any of these fail, this sub-project's acceptance criterion isn't met —
don't consider C5 live until they pass.

---

## Appendix — troubleshooting quick reference

| Symptom | Likely cause | Where to look |
|---|---|---|
| `db push` fails referencing an unknown role | `trax_app`/`trax_seed` bootstrap missing | [supabase/README.md](../supabase/README.md) prereq (a) — should already be done from C1 |
| Step 1.5's `tenants_for_current_user()` returns 0 rows for the smoke user | `auth.jwt()` not resolving in `security definer` on live Supabase | Step 1.5's fix note (rewrite to `current_setting('request.jwt.claims', ...)`) |
| A fresh signup still shows "no tenant access" | `whoami` 401'd, or the JWT has no `tenant_id` claim yet (session not refreshed post-org-creation) | `apps/web/src/lib/auth/useAuth.tsx`'s `tenantStatus` states; confirm `create_tenant_for_current_user` succeeded and `refreshSession()` ran |
| `recompute` jobs pile up `queued`, never `done` | Step 2's worker deploy didn't ship, or `WORKER_DATABASE_URL` isn't `trax_seed` | `railway logs -s worker`; `.claude/memory/lessons.md`'s worker-role entry |
| `recompute` jobs land `dead` immediately (not stuck `queued`, not retried) | Step 2's worker deploy shipped a STALE image that predates the `recompute` handler — `worker.run_once` dead-letters any `kind` with no registered handler, no retry | Step 6's dry-run note; confirm the deployed image is current, then redeploy the worker (Step 2) |
| `cron.job` has no rows after Step 5 | Extension not actually created, or the `cron.schedule(...)` call itself errored | Re-run Step 5's verify query; confirm `create extension pg_cron` didn't silently no-op (`select installed_version from pg_available_extensions where name='pg_cron';`); re-run `select cron.schedule(...)` directly and check for an error instead of a returned `jobid` |
| `502` on `/healthz` after Step 2 | Railway proxy/`PORT` mismatch, not a real crash | `.claude/memory/lessons.md`'s Railway entry — confirm `PORT=8000` is still set on `bff` |
