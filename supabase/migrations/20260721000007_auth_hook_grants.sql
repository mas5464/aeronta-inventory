-- C2: let GoTrue's supabase_auth_admin run the claims hook (security invoker).
-- On the local harness the role is created by auth_shim.sql; on live Supabase
-- it already exists.
grant usage on schema public to supabase_auth_admin;
grant execute on function public.custom_access_token_hook(jsonb) to supabase_auth_admin;
grant execute on function public.try_uuid(text) to supabase_auth_admin;
grant select on public.memberships, public.tenant_preferences to supabase_auth_admin;
create policy memberships_auth_hook_read on public.memberships
  for select to supabase_auth_admin using (true);
create policy tenant_preferences_auth_hook_read on public.tenant_preferences
  for select to supabase_auth_admin using (true);
