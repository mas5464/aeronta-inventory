-- supabase/migrations/20260723000012_billing_leads_and_org_rpc.sql
-- C4: marketing leads (anon insert-only) + self-serve org creation.

create table public.leads (
  id uuid primary key default gen_random_uuid(),
  name text, email text, company text, message text, source text,
  created_at timestamptz not null default now()
);
alter table public.leads enable row level security;
create policy leads_anon_insert on public.leads
  for insert to anon, authenticated with check (true);
grant insert on public.leads to anon, authenticated;
-- No select policy/grant → nobody reads leads via the API (team reads in Supabase).

-- Self-serve org creation: C1 RLS blocks direct member tenants-inserts; this
-- scoped create-for-self function is the sanctioned exception.
create function public.create_tenant_for_current_user(p_name text)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_uid  uuid := (auth.jwt()->>'sub')::uuid;
  v_base text := regexp_replace(lower(p_name), '[^a-z0-9]+', '-', 'g');
  v_slug text;
  v_id   uuid;
begin
  if v_uid is null then raise exception 'no authenticated user'; end if;
  v_base := trim(both '-' from v_base);
  if length(v_base) < 2 then v_base := 'org'; end if;
  v_id := null;
  -- Bounded retry: the random 6-char suffix makes collisions rare but not
  -- impossible; retry with a fresh suffix rather than let unique_violation
  -- escape to the caller.
  for attempt in 1..5 loop
    v_slug := left(v_base, 55) || '-' || substr(gen_random_uuid()::text, 1, 6);
    begin
      insert into public.tenants (slug, name, plan_tier)
        values (v_slug, p_name, 'trial') returning id into v_id;
      exit;
    exception when unique_violation then
      v_id := null;  -- fall through and retry with a new suffix
    end;
  end loop;
  if v_id is null then
    raise exception 'could not allocate unique slug for %', p_name;
  end if;
  insert into public.memberships (user_id, tenant_id, role)
    values (v_uid, v_id, 'owner');
  -- Bind the caller's tenant preference to the new org too. The C2 claims
  -- hook orders requested-claim > stored preference > newest membership; a
  -- caller with an existing preference row pointing at an OLDER tenant would
  -- otherwise mint a JWT that still carries that old tenant_id after
  -- refreshSession, silently binding the just-created org's checkout to the
  -- wrong tenant. Upsert so the newly created org always wins.
  insert into public.tenant_preferences (user_id, tenant_id)
    values (v_uid, v_id)
    on conflict (user_id) do update set tenant_id = excluded.tenant_id, updated_at = now();
  return v_id;
end;
$$;
revoke execute on function public.create_tenant_for_current_user(text) from public;
grant execute on function public.create_tenant_for_current_user(text) to authenticated;
