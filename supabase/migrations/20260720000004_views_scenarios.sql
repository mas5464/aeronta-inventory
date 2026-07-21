-- C1: seed-time view payloads + scenarios + BVR cache (spec §4 — heavy compute
-- stays out of the request path; static views are precomputed by pg/seed.py).
create table public.part_keys (
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  pn        text not null,
  location  text not null,
  key_stats jsonb not null,
  primary key (tenant_id, pn, location)
);

create table public.part_contexts (
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  pn        text not null,
  location  text not null,
  context   jsonb not null,
  primary key (tenant_id, pn, location)
);

create table public.tenant_snapshots (
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  kind      text not null check (kind in
            ('dashboard_static', 'forecast_summary', 'feeds_summary', 'current_policies')),
  payload   jsonb not null,
  seeded_at timestamptz not null default now(),
  primary key (tenant_id, kind)
);

create table public.scenarios (
  tenant_id   uuid not null references public.tenants (id) on delete cascade,
  scenario_id text not null,
  payload     jsonb not null,
  created_at  timestamptz not null default now(),
  primary key (tenant_id, scenario_id)
);

create table public.scenario_audit (
  id        bigint generated always as identity primary key,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  event     jsonb not null,
  at        timestamptz not null default now()
);
create index scenario_audit_tenant_idx on public.scenario_audit (tenant_id, at);

create table public.bvr_cache (
  tenant_id   uuid primary key references public.tenants (id) on delete cascade,
  report      jsonb not null,
  computed_at timestamptz not null default now()
);

alter table public.part_keys enable row level security;
alter table public.part_contexts enable row level security;
alter table public.tenant_snapshots enable row level security;
alter table public.scenarios enable row level security;
alter table public.scenario_audit enable row level security;
alter table public.bvr_cache enable row level security;

create policy part_keys_select on public.part_keys for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy part_contexts_select on public.part_contexts for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy tenant_snapshots_select on public.tenant_snapshots for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy scenarios_rw on public.scenarios for all to trax_app
  using (tenant_id = (select public.current_tenant_id()))
  with check (tenant_id = (select public.current_tenant_id()));
create policy scenario_audit_insert on public.scenario_audit for insert to trax_app
  with check (tenant_id = (select public.current_tenant_id()));
create policy scenario_audit_select on public.scenario_audit for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy bvr_cache_rw on public.bvr_cache for all to trax_app
  using (tenant_id = (select public.current_tenant_id()))
  with check (tenant_id = (select public.current_tenant_id()));

grant select on public.part_keys, public.part_contexts, public.tenant_snapshots to trax_app;
grant select, insert, update, delete on public.scenarios to trax_app;
grant select, insert on public.scenario_audit to trax_app;
grant select, insert, update, delete on public.bvr_cache to trax_app;
grant usage, select on all sequences in schema public to trax_app;
grant all on public.part_keys, public.part_contexts, public.tenant_snapshots,
  public.scenarios, public.scenario_audit, public.bvr_cache to trax_seed;
