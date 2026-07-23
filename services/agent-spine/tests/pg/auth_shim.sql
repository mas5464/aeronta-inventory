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
