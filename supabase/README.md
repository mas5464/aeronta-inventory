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
