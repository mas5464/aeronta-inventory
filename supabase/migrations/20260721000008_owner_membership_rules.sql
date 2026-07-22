-- C3 Task 0b: move owner-specific membership rules into RLS (defense in depth behind
-- the app-layer _require_owner / last-owner guard). Only an owner may create/modify/
-- delete an owner-role membership; admins keep managing planner/viewer rows.
create or replace function public.current_tenant_role() returns text
language sql stable as $$
  select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
    ->> 'tenant_role'
$$;

drop policy if exists memberships_insert on public.memberships;
drop policy if exists memberships_update on public.memberships;
drop policy if exists memberships_delete on public.memberships;

create policy memberships_insert on public.memberships for insert to trax_app
  with check (
    tenant_id = (select public.current_tenant_id())
    and (select public.current_tenant_role()) in ('admin', 'owner')
    -- creating an owner requires owner
    and (role <> 'owner' or (select public.current_tenant_role()) = 'owner')
  );

create policy memberships_update on public.memberships for update to trax_app
  using (
    tenant_id = (select public.current_tenant_id())
    and (select public.current_tenant_role()) in ('admin', 'owner')
    -- modifying an existing owner row requires owner
    and (role <> 'owner' or (select public.current_tenant_role()) = 'owner')
  )
  with check (
    tenant_id = (select public.current_tenant_id())
    -- setting a row TO owner requires owner
    and (role <> 'owner' or (select public.current_tenant_role()) = 'owner')
  );

create policy memberships_delete on public.memberships for delete to trax_app
  using (
    tenant_id = (select public.current_tenant_id())
    and (select public.current_tenant_role()) in ('admin', 'owner')
    and (role <> 'owner' or (select public.current_tenant_role()) = 'owner')
  );
