-- C2: auth bootstrap + user management writes + jobs + ledger idempotency index.

-- (1) Slug->uuid resolve usable by trax_app BEFORE tenant claims exist (the BFF's
-- boot/request path). SECURITY DEFINER: runs as the migration owner (postgres on
-- live Supabase), which sees public.tenants regardless of RLS. Retires the
-- "DATABASE_URL must be a bypassrls role" workaround from C1 Task 13.
create function public.resolve_tenant_slug(p_slug text) returns uuid
language sql stable security definer
set search_path = public
as $$
  select id from public.tenants where slug = p_slug
$$;
revoke all on function public.resolve_tenant_slug(text) from public;
grant execute on function public.resolve_tenant_slug(text) to trax_app, trax_seed;

-- (2) Memberships writes for user management (C1 left trax_app read-only by design).
-- Admin/owner of the CURRENT tenant may manage that tenant's memberships only.
create policy memberships_insert on public.memberships for insert to trax_app
  with check (
    tenant_id = (select public.current_tenant_id())
    and (select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
      ->> 'tenant_role') in ('admin', 'owner')
  );
create policy memberships_update on public.memberships for update to trax_app
  using (
    tenant_id = (select public.current_tenant_id())
    and (select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
      ->> 'tenant_role') in ('admin', 'owner')
  )
  with check (tenant_id = (select public.current_tenant_id()));
create policy memberships_delete on public.memberships for delete to trax_app
  using (
    tenant_id = (select public.current_tenant_id())
    and (select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
      ->> 'tenant_role') in ('admin', 'owner')
  );
grant insert, update, delete on public.memberships to trax_app;

-- (3) Jobs queue (spec §5): C2 ships the table + an idle worker; C3 registers handlers.
create table public.jobs (
  id         bigint generated always as identity primary key,
  tenant_id  uuid not null references public.tenants (id) on delete cascade,
  kind       text not null check (kind in ('ingest', 'recompute', 'bvr')),
  status     text not null default 'queued'
             check (status in ('queued', 'running', 'done', 'failed', 'dead')),
  payload    jsonb not null default '{}'::jsonb,
  attempts   integer not null default 0,
  claimed_at timestamptz,
  finished_at timestamptz,
  error      text,
  created_at timestamptz not null default now()
);
create index jobs_claim_idx on public.jobs (status, id);
create index jobs_tenant_idx on public.jobs (tenant_id, created_at desc);

alter table public.jobs enable row level security;
create policy jobs_select on public.jobs for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy jobs_insert on public.jobs for insert to trax_app
  with check (tenant_id = (select public.current_tenant_id()));
grant select, insert on public.jobs to trax_app;
grant all on public.jobs to trax_seed;
grant usage, select on all sequences in schema public to trax_app;

-- (4) Ledger idempotency-key expression index (C1 final-review pre-flight #2):
-- PgWritebackTarget._replay filters on entry->>'idempotency_key' per write.
create index writeback_ledger_idem_idx
  on public.writeback_ledger (tenant_id, (entry->>'idempotency_key'));

-- (5) Tenant switching: a fresh token mint carries no "requested tenant" claim,
-- so the hook needs a stored preference. Users write ONLY their own row (RLS on
-- the JWT sub); membership validity is enforced by the hook itself at mint time.
create table public.tenant_preferences (
  user_id   uuid primary key,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  updated_at timestamptz not null default now()
);
alter table public.tenant_preferences enable row level security;
create policy tenant_preferences_own on public.tenant_preferences for all to trax_app
  using (user_id = (select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb ->> 'sub')::uuid)
  with check (user_id = (select coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb ->> 'sub')::uuid);
grant select, insert, update, delete on public.tenant_preferences to trax_app;
grant all on public.tenant_preferences to trax_seed;

-- (6) Hook v2: preference-aware selection. Priority: explicit requested claim
-- (legacy path, still validated) > stored preference (validated) > most-recent
-- membership. Foreign/stale preferences fall back — never pass through.
create or replace function public.custom_access_token_hook(event jsonb) returns jsonb
language plpgsql stable as $$
declare
  uid uuid := public.try_uuid(event->>'user_id');
  requested uuid := public.try_uuid(nullif(event->'claims'->>'tenant_id', ''));
  preferred uuid;
  m record;
begin
  if uid is null then
    return jsonb_set(event, '{claims}', (event->'claims') - 'tenant_id' - 'tenant_role');
  end if;
  select tenant_id into preferred from public.tenant_preferences where user_id = uid;
  select tenant_id, role into m
  from public.memberships
  where user_id = uid
  order by (tenant_id = requested) desc nulls last,
           (tenant_id = preferred) desc nulls last,
           created_at desc
  limit 1;
  if m is null then
    return jsonb_set(event, '{claims}', (event->'claims') - 'tenant_id' - 'tenant_role');
  end if;
  return jsonb_set(
    event, '{claims}',
    (event->'claims')
      || jsonb_build_object('tenant_id', m.tenant_id::text, 'tenant_role', m.role)
  );
end;
$$;
grant execute on function public.custom_access_token_hook(jsonb) to trax_seed;
