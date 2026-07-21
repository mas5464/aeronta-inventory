-- C2 live-deploy fix: decouple current_tenant_id() from the auth schema.
--
-- On real Supabase, custom roles (trax_app/trax_seed) get NO usage on schema auth,
-- and `grant usage on schema auth` issued by the postgres role silently no-ops
-- (postgres holds usage without GRANT OPTION) — so every RLS policy inlining
-- auth.jwt() fails with permission-denied for the app roles. Read the
-- request.jwt.claims GUC directly instead: auth.jwt() is defined over the very
-- same GUC, so semantics are byte-identical for real Supabase JWT requests AND
-- for the BFF's tenant_conn (which sets the GUC itself), while removing the
-- cross-schema dependency entirely. Works unchanged on the plain-Postgres
-- test harness.
create or replace function public.current_tenant_id() returns uuid
language sql stable as $$
  select nullif(
    coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
      ->> 'tenant_id',
    ''
  )::uuid
$$;
