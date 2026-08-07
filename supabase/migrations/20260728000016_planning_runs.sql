-- Phase 7/9: immutable advisory portfolio planning runs + persisted selections.
-- JSONB remains the contract source of truth; scalar columns are query accelerators.

alter table public.tenant_snapshots
  drop constraint if exists tenant_snapshots_kind_check;
alter table public.tenant_snapshots
  add constraint tenant_snapshots_kind_check
  check (kind in (
    'dashboard_static',
    'forecast_summary',
    'feeds_summary',
    'current_policies',
    'scenario_inputs',
    'planning_inputs'
  ));

alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs
  add constraint jobs_kind_check
  check (kind in ('ingest', 'recompute', 'bvr', 'planning'));

create table public.planning_runs (
  tenant_id            uuid not null references public.tenants (id) on delete cascade,
  run_id               uuid not null default gen_random_uuid(),
  planning_fingerprint text not null
                       check (planning_fingerprint ~ '^planning_[0-9a-f]{64}$'),
  contract_version     text not null check (length(contract_version) > 0),
  parent_run_id        uuid,
  parent_planning_fingerprint text
                       check (
                         parent_planning_fingerprint is null
                         or parent_planning_fingerprint ~ '^planning_[0-9a-f]{64}$'
                       ),
  parent_source_snapshot_hash text
                       check (
                         parent_source_snapshot_hash is null
                         or length(parent_source_snapshot_hash) > 0
                       ),
  assumption_diff      jsonb not null default '[]'::jsonb
                       check (jsonb_typeof(assumption_diff) = 'array'),
  status               text not null default 'queued'
                       check (status in
                         ('queued', 'running', 'completed', 'infeasible', 'failed')),
  scope_kind           text not null default 'explicit'
                       check (scope_kind in ('explicit', 'all_eligible')),
  scope_preview        jsonb not null default '[]'::jsonb
                       check (jsonb_typeof(scope_preview) = 'array'),
  source_snapshot_hash text not null check (length(source_snapshot_hash) > 0),
  source_generation_hash text not null
                       check (
                         source_generation_hash
                         ~ '^planning_generation_[0-9a-f]{64}$'
                       ),
  explicit_scope       jsonb not null
                       check (jsonb_typeof(explicit_scope) = 'array'),
  key_count            integer not null check (key_count > 0),
  menu_count           integer not null check (menu_count > 0),
  menus_fingerprint    text not null
                       check (menus_fingerprint ~ '^planning_menus_[0-9a-f]{64}$'),
  candidate_count      integer not null check (candidate_count >= menu_count),
  feasible_candidate_count integer not null
                       check (
                         feasible_candidate_count >= 0
                         and feasible_candidate_count <= candidate_count
                       ),
  coverage             jsonb not null check (jsonb_typeof(coverage) = 'object'),
  budget               numeric not null check (budget >= 0),
  horizon_days         integer not null check (horizon_days > 0),
  currency             text not null check (currency ~ '^[A-Z]{3}$'),
  model_profile        jsonb not null default '{}'::jsonb
                       check (jsonb_typeof(model_profile) = 'object'),
  request              jsonb not null
                       check (
                         jsonb_typeof(request) = 'object'
                         and not (request ? 'menus')
                       ),
  advisory_only        boolean not null default true check (advisory_only),
  progress_completed   integer not null default 0 check (progress_completed >= 0),
  progress_total       integer not null check (progress_total > 0),
  summary              jsonb
                       check (summary is null or jsonb_typeof(summary) = 'object'),
  result               jsonb
                       check (result is null or jsonb_typeof(result) = 'object'),
  detail               jsonb not null default '{}'::jsonb
                       check (jsonb_typeof(detail) = 'object'),
  solver               jsonb
                       check (solver is null or jsonb_typeof(solver) = 'object'),
  warnings             jsonb not null default '[]'::jsonb
                       check (jsonb_typeof(warnings) = 'array'),
  warning_count        integer not null default 0 check (warning_count >= 0),
  skipped_keys         jsonb not null default '[]'::jsonb
                       check (jsonb_typeof(skipped_keys) = 'array'),
  skipped_key_count    integer not null default 0 check (skipped_key_count >= 0),
  submitted_by         text not null check (length(submitted_by) > 0),
  attempts             integer not null default 0 check (attempts >= 0),
  claimed_at           timestamptz,
  started_at           timestamptz,
  finished_at          timestamptz,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now(),
  primary key (tenant_id, run_id),
  unique (tenant_id, planning_fingerprint, source_generation_hash),
  foreign key (tenant_id, parent_run_id)
    references public.planning_runs (tenant_id, run_id),
  check (
    (
      parent_run_id is null
      and parent_planning_fingerprint is null
      and parent_source_snapshot_hash is null
    )
    or (
      parent_run_id is not null
      and parent_planning_fingerprint is not null
      and parent_source_snapshot_hash is not null
    )
  ),
  check (progress_completed <= progress_total),
  check (menu_count = key_count),
  check (
    (
      scope_kind = 'explicit'
      and key_count = jsonb_array_length(explicit_scope)
      and scope_preview = explicit_scope
    )
    or (
      scope_kind = 'all_eligible'
      and jsonb_array_length(explicit_scope) = 0
      and jsonb_array_length(scope_preview) > 0
      and jsonb_array_length(scope_preview) <= 10
    )
  ),
  check (progress_total = key_count),
  check (
    (status in ('queued', 'running') and finished_at is null)
    or (status in ('completed', 'infeasible', 'failed') and finished_at is not null)
  )
);
create index planning_runs_recent_idx
  on public.planning_runs (tenant_id, created_at desc, run_id);
create index planning_runs_snapshot_idx
  on public.planning_runs (tenant_id, source_snapshot_hash);
create index planning_runs_generation_idx
  on public.planning_runs (tenant_id, source_generation_hash);

create table public.planning_run_menus (
  tenant_id      uuid not null,
  run_id         uuid not null,
  ordinal        integer not null check (ordinal >= 0),
  decision_key   text not null check (length(decision_key) > 0),
  candidate_count integer not null check (candidate_count > 0),
  menu           jsonb not null
                 check (
                   jsonb_typeof(menu) = 'object'
                   and menu #>> '{frontier,decision_key}' = decision_key
                   and jsonb_typeof(menu #> '{frontier,candidates}') = 'array'
                   and jsonb_array_length(menu #> '{frontier,candidates}')
                       = candidate_count
                 ),
  primary key (tenant_id, run_id, decision_key),
  unique (tenant_id, run_id, ordinal),
  foreign key (tenant_id, run_id)
    references public.planning_runs (tenant_id, run_id) on delete cascade
);
create index planning_run_menus_run_idx
  on public.planning_run_menus (tenant_id, run_id, ordinal);

create table public.planning_run_selections (
  tenant_id            uuid not null,
  run_id               uuid not null,
  decision_key         text not null,
  current_candidate_id text not null,
  selected_candidate_id text not null,
  selected_is_no_change boolean not null,
  acquisition_cash     numeric not null check (acquisition_cash >= 0),
  objective            numeric not null,
  selection            jsonb not null check (jsonb_typeof(selection) = 'object'),
  detail               jsonb not null default '{}'::jsonb
                       check (jsonb_typeof(detail) = 'object'),
  primary key (tenant_id, run_id, decision_key),
  foreign key (tenant_id, run_id)
    references public.planning_runs (tenant_id, run_id) on delete cascade,
  foreign key (tenant_id, run_id, decision_key)
    references public.planning_run_menus (tenant_id, run_id, decision_key)
    on delete cascade
);
create index planning_run_selections_run_idx
  on public.planning_run_selections (tenant_id, run_id, decision_key);

create unique index jobs_planning_run_idx
  on public.jobs (tenant_id, (payload->>'run_id'))
  where kind = 'planning';

create function public.enforce_planning_run_immutability() returns trigger
language plpgsql
set search_path = public
as $$
declare
  stored_menu_count integer;
  stored_selection_count integer;
begin
  if old.tenant_id is distinct from new.tenant_id
     or old.run_id is distinct from new.run_id
     or old.planning_fingerprint is distinct from new.planning_fingerprint
     or old.contract_version is distinct from new.contract_version
     or old.parent_run_id is distinct from new.parent_run_id
     or old.parent_planning_fingerprint is distinct from new.parent_planning_fingerprint
     or old.parent_source_snapshot_hash is distinct from new.parent_source_snapshot_hash
     or old.assumption_diff is distinct from new.assumption_diff
     or old.scope_kind is distinct from new.scope_kind
     or old.scope_preview is distinct from new.scope_preview
     or old.source_snapshot_hash is distinct from new.source_snapshot_hash
     or old.source_generation_hash is distinct from new.source_generation_hash
     or old.explicit_scope is distinct from new.explicit_scope
     or old.key_count is distinct from new.key_count
     or old.menu_count is distinct from new.menu_count
     or old.menus_fingerprint is distinct from new.menus_fingerprint
     or old.candidate_count is distinct from new.candidate_count
     or old.feasible_candidate_count is distinct from new.feasible_candidate_count
     or old.coverage is distinct from new.coverage
     or old.budget is distinct from new.budget
     or old.horizon_days is distinct from new.horizon_days
     or old.currency is distinct from new.currency
     or old.model_profile is distinct from new.model_profile
     or old.request is distinct from new.request
     or old.advisory_only is distinct from new.advisory_only
     or old.submitted_by is distinct from new.submitted_by
     or old.created_at is distinct from new.created_at then
    raise exception 'planning run immutable input cannot be changed';
  end if;

  if old.status in ('completed', 'infeasible', 'failed') then
    raise exception 'terminal planning run cannot be changed';
  end if;

  if not (
    new.status = old.status
    or (old.status = 'queued' and new.status in ('running', 'failed'))
    or (
      old.status = 'running'
      and new.status in ('queued', 'completed', 'infeasible', 'failed')
    )
  ) then
    raise exception 'invalid planning run status transition: % -> %', old.status, new.status;
  end if;

  select count(*)
  into stored_menu_count
  from public.planning_run_menus
  where tenant_id = new.tenant_id and run_id = new.run_id;

  if new.status in ('running', 'completed')
     and stored_menu_count <> new.menu_count then
    raise exception 'planning run menu count does not reconcile';
  end if;

  select count(*)
  into stored_selection_count
  from public.planning_run_selections
  where tenant_id = new.tenant_id and run_id = new.run_id;

  if new.status = 'completed'
     and stored_selection_count <> new.key_count then
    raise exception 'planning run selection count does not reconcile';
  elsif new.status <> 'completed' and stored_selection_count <> 0 then
    raise exception 'non-completed planning run cannot retain selections';
  end if;

  new.updated_at := now();
  return new;
end;
$$;
create trigger planning_runs_immutable
before update on public.planning_runs
for each row execute function public.enforce_planning_run_immutability();

create function public.reject_planning_selection_update() returns trigger
language plpgsql
set search_path = public
as $$
begin
  raise exception 'planning run child rows are immutable';
end;
$$;
create trigger planning_run_menus_immutable
before update on public.planning_run_menus
for each row execute function public.reject_planning_selection_update();
create trigger planning_run_selections_immutable
before update on public.planning_run_selections
for each row execute function public.reject_planning_selection_update();

create function public.enforce_planning_menu_insert() returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  parent_status text;
  expected_count integer;
begin
  select status, menu_count
  into parent_status, expected_count
  from public.planning_runs
  where tenant_id = new.tenant_id and run_id = new.run_id
  for update;

  if parent_status is distinct from 'queued' then
    raise exception 'planning menu parent is sealed';
  end if;
  if new.ordinal >= expected_count then
    raise exception 'planning menu scope is sealed';
  end if;
  return new;
end;
$$;
create trigger planning_run_menus_parent_queued
before insert on public.planning_run_menus
for each row execute function public.enforce_planning_menu_insert();

create function public.enforce_planning_selection_insert() returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  parent_status text;
begin
  select status
  into parent_status
  from public.planning_runs
  where tenant_id = new.tenant_id and run_id = new.run_id
  for update;

  if parent_status is distinct from 'running' then
    raise exception 'planning selection parent is not running';
  end if;
  return new;
end;
$$;
create trigger planning_run_selections_parent_running
before insert on public.planning_run_selections
for each row execute function public.enforce_planning_selection_insert();

revoke all on function public.enforce_planning_run_immutability()
  from public, anon, authenticated, service_role, trax_app, trax_seed;
revoke all on function public.reject_planning_selection_update()
  from public, anon, authenticated, service_role, trax_app, trax_seed;
revoke all on function public.enforce_planning_menu_insert()
  from public, anon, authenticated, service_role, trax_app, trax_seed;
revoke all on function public.enforce_planning_selection_insert()
  from public, anon, authenticated, service_role, trax_app, trax_seed;

alter table public.planning_runs enable row level security;
alter table public.planning_run_menus enable row level security;
alter table public.planning_run_selections enable row level security;

create policy planning_runs_select on public.planning_runs for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy planning_runs_insert on public.planning_runs for insert to trax_app
  with check (
    tenant_id = (select public.current_tenant_id())
    and status = 'queued'
    and summary is null
    and result is null
    and solver is null
    and finished_at is null
    and progress_completed = 0
    and attempts = 0
    and claimed_at is null
    and started_at is null
    and detail = '{}'::jsonb
    and warnings = '[]'::jsonb
    and warning_count = 0
    and skipped_key_count = coalesce(
      (coverage->>'skipped_key_count')::integer,
      0
    )
    and skipped_keys = (
      case
        when skipped_key_count = 0 then '[]'::jsonb
        else jsonb_build_array(
          jsonb_build_object(
            'reason_code', 'missing_candidate_frontier',
            'count', skipped_key_count
          )
        )
      end
    )
    and advisory_only
    and submitted_by = (
      select coalesce(
        nullif(current_setting('request.jwt.claims', true), ''),
        '{}'
      )::jsonb ->> 'sub'
    )
    and (
      select coalesce(
        nullif(current_setting('request.jwt.claims', true), ''),
        '{}'
      )::jsonb ->> 'tenant_role'
    ) in ('planner', 'admin', 'owner')
  );
create policy planning_run_selections_select
  on public.planning_run_selections for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy planning_run_menus_insert
  on public.planning_run_menus for insert to trax_app
  with check (
    tenant_id = (select public.current_tenant_id())
    and exists (
      select 1
      from public.planning_runs r
      where r.tenant_id = planning_run_menus.tenant_id
        and r.run_id = planning_run_menus.run_id
        and r.status = 'queued'
        and planning_run_menus.ordinal < r.menu_count
    )
  );

drop policy jobs_insert on public.jobs;
create policy jobs_insert on public.jobs for insert to trax_app
  with check (
    tenant_id = (select public.current_tenant_id())
    and (
      kind <> 'planning'
      or (
        (
          select coalesce(
            nullif(current_setting('request.jwt.claims', true), ''),
            '{}'
          )::jsonb ->> 'tenant_role'
        ) in ('planner', 'admin', 'owner')
        and exists (
          select 1
          from public.planning_runs r
          where r.tenant_id = jobs.tenant_id
            and r.run_id::text = jobs.payload->>'run_id'
            and r.status = 'queued'
        )
      )
    )
  );

grant select, insert on public.planning_runs to trax_app;
grant insert on public.planning_run_menus to trax_app;
grant select on public.planning_run_selections to trax_app;
grant select, insert, update on public.planning_runs to trax_seed;
grant select, insert on public.planning_run_menus to trax_seed;
grant select, insert on public.planning_run_selections to trax_seed;
grant usage, select on all sequences in schema public to trax_app;
