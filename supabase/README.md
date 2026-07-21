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
