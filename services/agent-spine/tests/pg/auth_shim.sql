-- Test-harness-only Supabase compatibility shim for plain Postgres.
-- Real Supabase provides schema auth + roles; never ship this as a migration.
create schema if not exists auth;

create or replace function auth.jwt() returns jsonb
language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
$$;

create or replace function auth.uid() returns uuid
language sql stable as $$
  select nullif(auth.jwt()->>'sub', '')::uuid
$$;

do $$ begin
  if not exists (select from pg_roles where rolname = 'anon') then
    create role anon nologin;
  end if;
  if not exists (select from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin;
  end if;
  -- Group D (C5 final review fix): real Supabase's platform baseline grants
  -- this role EXECUTE on every public-schema function by default, same as
  -- anon/authenticated — needed here so a migration's explicit revoke from
  -- it (see 20260724000014_enqueue_due_recomputes.sql) has a real role to
  -- target, and so tests can `set role service_role` to prove it.
  if not exists (select from pg_roles where rolname = 'service_role') then
    create role service_role nologin;
  end if;
  if not exists (select from pg_roles where rolname = 'trax_app') then
    create role trax_app login password 'trax_app' nobypassrls;
  end if;
  if not exists (select from pg_roles where rolname = 'trax_seed') then
    create role trax_seed login password 'trax_seed' bypassrls;
  end if;
  if not exists (select from pg_roles where rolname = 'supabase_auth_admin') then
    create role supabase_auth_admin nologin;
  end if;
end $$;

grant usage on schema auth to trax_app, trax_seed;
grant execute on all functions in schema auth to trax_app, trax_seed;

-- Group D (C5 final review fix): reproduces the specific piece of real
-- Supabase's platform baseline the Group D bug depends on — `alter default
-- privileges in schema public grant all on functions to postgres, anon,
-- authenticated, service_role` — so that a migration's `revoke ... from
-- public` (which has NO effect on these explicit, named-role grants) is
-- actually exercisable here. Vanilla Postgres does not apply this on its
-- own; without it, `anon`/`authenticated`/`service_role` would never have
-- had EXECUTE on a newly-created function in THIS throwaway container
-- (Postgres's own built-in default only grants new functions to PUBLIC),
-- so a test asserting "denied after the fix" would trivially pass even
-- WITHOUT the fix's extra revoke, having caught nothing. Applies to every
-- function created by THIS session's role from here on — i.e. every
-- migration `apply_migrations()` runs next, on the same connection —
-- mirroring how Supabase's own migration runner creates functions as the
-- same role its platform bootstrap already altered default privileges for.
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
