-- C1: durable decision lifecycle (spec §4). Payload JSONB is source of truth;
-- scalar columns are derived query accelerators for the queue's sort/filter.
create table public.recommendations (
  tenant_id        uuid not null references public.tenants (id) on delete cascade,
  rec_id           text not null,
  status           text not null default 'pending'
                   check (status in ('pending', 'approved', 'rejected', 'deferred')),
  pn               text not null,
  location         text not null,
  tier             smallint not null,
  rec_type         text not null,
  criticality_tier smallint not null,
  aog_level        smallint not null default 0,
  confidence       numeric not null,
  cost_impact      numeric not null,
  priority         numeric not null default 0,
  approvable       boolean not null,
  rec              jsonb not null,
  outcome          jsonb not null,
  reject_reason    text,
  reject_detail    text,
  deferred_until   timestamptz,
  decided_at       timestamptz,
  primary key (tenant_id, rec_id)
);
create index recommendations_queue_idx
  on public.recommendations (tenant_id, status, priority desc);
create index recommendations_key_idx on public.recommendations (tenant_id, pn, location);

create table public.decisions (
  id        bigint generated always as identity primary key,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  rec_id    text,
  action    text not null check (action in
            ('approve', 'reject', 'defer', 'bulk_approve', 'rollback', 'kill_switch')),
  payload   jsonb not null default '{}'::jsonb,
  principal text not null default 'planner',
  at        timestamptz not null default now()
);
create index decisions_tenant_idx on public.decisions (tenant_id, at desc);

create table public.writeback_ledger (
  tenant_id  uuid not null references public.tenants (id) on delete cascade,
  pn         text not null,
  location   text not null,
  version    integer not null check (version > 0),
  entry      jsonb not null,
  changed_at timestamptz not null,
  primary key (tenant_id, pn, location, version)
);
create index writeback_ledger_tenant_idx on public.writeback_ledger (tenant_id, pn, location);

create table public.kill_switches (
  tenant_id uuid primary key references public.tenants (id) on delete cascade,
  engaged   boolean not null default false,
  updated_at timestamptz not null default now()
);

alter table public.recommendations enable row level security;
alter table public.decisions enable row level security;
alter table public.writeback_ledger enable row level security;
alter table public.kill_switches enable row level security;

create policy recommendations_rw on public.recommendations for all to trax_app
  using (tenant_id = (select public.current_tenant_id()))
  with check (tenant_id = (select public.current_tenant_id()));
create policy decisions_insert on public.decisions for insert to trax_app
  with check (tenant_id = (select public.current_tenant_id()));
create policy decisions_select on public.decisions for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy ledger_insert on public.writeback_ledger for insert to trax_app
  with check (tenant_id = (select public.current_tenant_id()));
create policy ledger_select on public.writeback_ledger for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy kill_switches_rw on public.kill_switches for all to trax_app
  using (tenant_id = (select public.current_tenant_id()))
  with check (tenant_id = (select public.current_tenant_id()));

-- append-only enforcement: no UPDATE/DELETE policies exist for decisions/ledger,
-- and the grants below don't include them either (belt and braces).
grant select, insert, update, delete on public.recommendations to trax_app;
grant select, insert on public.decisions to trax_app;
grant select, insert on public.writeback_ledger to trax_app;
grant select, insert, update on public.kill_switches to trax_app;
grant usage, select on all sequences in schema public to trax_app;
grant all on public.recommendations, public.decisions,
  public.writeback_ledger, public.kill_switches to trax_seed;
