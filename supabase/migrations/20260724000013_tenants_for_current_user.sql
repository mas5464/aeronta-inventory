-- supabase/migrations/20260724000013_tenants_for_current_user.sql
-- C5: list the CALLER's tenant memberships.
--
-- Normal RLS cannot express this: `memberships` is scoped to
-- current_tenant_id(), so a user connected as tenant A cannot see their
-- tenant-B row. This is the same sanctioned SECURITY DEFINER exception as
-- C4's create_tenant_for_current_user — strictly caller-scoped, no arguments,
-- so it can only ever return the caller's own rows.
create function public.tenants_for_current_user()
returns table (tenant_uuid uuid, slug text, name text, role text)
language sql
stable
security definer
set search_path = public
as $$
  select t.id, t.slug, t.name, m.role
    from public.memberships m
    join public.tenants t on t.id = m.tenant_id
   where m.user_id = (auth.jwt()->>'sub')::uuid
   order by t.slug
$$;

revoke execute on function public.tenants_for_current_user() from public;
grant execute on function public.tenants_for_current_user() to authenticated, trax_app;
