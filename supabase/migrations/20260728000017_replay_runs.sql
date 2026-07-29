-- Phase 11: immutable, tenant-scoped no-lookahead replay runs and advisory
-- shadow scorecards. Browsers submit only bounded config plus an opaque
-- service-seeded universe reference; large trusted inputs and terminal
-- evidence are normalized into immutable child rows.

alter table public.jobs drop constraint if exists jobs_kind_check;
alter table public.jobs
  add constraint jobs_kind_check
  check (kind in ('ingest', 'recompute', 'bvr', 'planning', 'replay'));

create table public.replay_universes (
  tenant_id              uuid not null references public.tenants (id) on delete cascade,
  universe_ref           text not null check (
                           length(universe_ref) between 1 and 256
                         ),
  universe_id            text not null check (
                           length(universe_id) between 1 and 256
                         ),
  universe_sha256        text not null check (universe_sha256 ~ '^[0-9a-f]{64}$'),
  trusted_input_sha256   text not null check (
                           trusted_input_sha256 ~ '^[0-9a-f]{64}$'
                         ),
  contract_version       text not null check (contract_version = 'replay.v1'),
  currency               text not null check (currency ~ '^[A-Z]{3}$'),
  expected_decision_count integer not null check (expected_decision_count > 0),
  observation_count      integer not null check (observation_count >= 0),
  exclusion_count        integer not null check (exclusion_count >= 0),
  created_at             timestamptz not null default now(),
  primary key (tenant_id, universe_ref),
  unique (tenant_id, trusted_input_sha256),
  check (observation_count + exclusion_count = expected_decision_count)
);

create table public.replay_universe_rows (
  tenant_id       uuid not null,
  universe_ref    text not null,
  ordinal         integer not null check (ordinal >= 0),
  row_kind        text not null check (row_kind in ('observation', 'exclusion')),
  observation_id  text not null check (length(observation_id) > 0),
  decision_key    text not null check (length(decision_key) > 0),
  as_of           timestamptz not null,
  horizon_end     timestamptz not null,
  payload         jsonb not null check (
                    jsonb_typeof(payload) = 'object'
                    and payload->>'observation_id' = observation_id
                    and payload->>'decision_key' = decision_key
                  ),
  primary key (tenant_id, universe_ref, ordinal),
  unique (tenant_id, universe_ref, observation_id),
  unique (
    tenant_id,
    universe_ref,
    decision_key,
    as_of,
    horizon_end
  ),
  foreign key (tenant_id, universe_ref)
    references public.replay_universes (tenant_id, universe_ref) on delete cascade,
  check (
    horizon_end > as_of
    and (payload->>'as_of')::timestamptz = as_of
    and (payload->>'horizon_end')::timestamptz = horizon_end
  )
);
create index replay_universe_rows_order_idx
  on public.replay_universe_rows (tenant_id, universe_ref, ordinal);

create table public.replay_runs (
  tenant_id              uuid not null references public.tenants (id) on delete cascade,
  replay_id              uuid not null default gen_random_uuid(),
  replay_fingerprint     text not null
                         check (replay_fingerprint ~ '^replay_[0-9a-f]{64}$'),
  input_sha256           text not null check (input_sha256 ~ '^[0-9a-f]{64}$'),
  contract_version       text not null check (contract_version = 'replay.v1'),
  status                 text not null default 'queued'
                         check (status in ('queued', 'running', 'completed', 'failed')),
  universe_ref           text not null check (
                           length(universe_ref) between 1 and 256
                         ),
  universe_id            text not null check (
                           length(universe_id) between 1 and 256
                         ),
  universe_sha256        text not null check (universe_sha256 ~ '^[0-9a-f]{64}$'),
  comparison_rule        text not null
                         check (comparison_rule in ('matched_budget', 'matched_service')),
  expected_decision_count integer not null check (expected_decision_count > 0),
  request                 jsonb not null check (
                           jsonb_typeof(request) = 'object'
                           and not (request ? 'universe_decisions')
                           and not (request ? 'observations')
                           and not (request ? 'exclusions')
                         ),
  advisory_only           boolean not null default true check (advisory_only),
  scorecard               jsonb,
  coverage_rate           numeric check (
                            coverage_rate is null
                            or (coverage_rate >= 0 and coverage_rate <= 1)
                          ),
  detail                  jsonb not null default '{}'::jsonb
                          check (jsonb_typeof(detail) = 'object'),
  submitted_by            text not null,
  attempts                integer not null default 0 check (attempts >= 0),
  claimed_at              timestamptz,
  started_at              timestamptz,
  finished_at             timestamptz,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  primary key (tenant_id, replay_id),
  unique (tenant_id, replay_fingerprint),
  foreign key (tenant_id, universe_ref)
    references public.replay_universes (tenant_id, universe_ref),
  check (
    (
      status in ('queued', 'running')
      and scorecard is null
      and coverage_rate is null
      and finished_at is null
    )
    or (
      status = 'completed'
      and scorecard is not null
      and jsonb_typeof(scorecard) = 'object'
      and not (scorecard ? 'universe_decisions')
      and not (scorecard ? 'exclusions')
      and not (scorecard ? 'observation_lineage')
      and not (scorecard ? 'cohorts')
      and not (scorecard ? 'source_snapshot_hashes')
      and not (scorecard ? 'planning_fingerprints')
      and coverage_rate is not null
      and finished_at is not null
    )
    or (
      status = 'failed'
      and scorecard is null
      and coverage_rate is null
      and finished_at is not null
    )
  )
);

create index replay_runs_recent_idx
  on public.replay_runs (tenant_id, created_at desc, replay_id);
create index replay_runs_universe_idx
  on public.replay_runs (tenant_id, universe_ref, created_at desc);
create unique index jobs_replay_run_idx
  on public.jobs (tenant_id, (payload->>'replay_id'))
  where kind = 'replay';

create table public.replay_run_lineage (
  tenant_id      uuid not null,
  replay_id      uuid not null,
  observation_id text not null,
  decision_key   text not null,
  as_of          timestamptz not null,
  horizon_end    timestamptz not null,
  cohort_id      text not null,
  lineage        jsonb not null check (jsonb_typeof(lineage) = 'object'),
  primary key (tenant_id, replay_id, observation_id),
  foreign key (tenant_id, replay_id)
    references public.replay_runs (tenant_id, replay_id) on delete cascade,
  check (horizon_end > as_of)
);
create index replay_run_lineage_page_idx
  on public.replay_run_lineage (tenant_id, replay_id, observation_id);

create table public.replay_run_exclusions (
  tenant_id      uuid not null,
  replay_id      uuid not null,
  observation_id text not null,
  decision_key   text not null,
  as_of          timestamptz not null,
  horizon_end    timestamptz not null,
  reason_code    text not null,
  exclusion      jsonb not null check (jsonb_typeof(exclusion) = 'object'),
  primary key (tenant_id, replay_id, observation_id),
  foreign key (tenant_id, replay_id)
    references public.replay_runs (tenant_id, replay_id) on delete cascade,
  check (horizon_end > as_of)
);
create index replay_run_exclusions_page_idx
  on public.replay_run_exclusions (
    tenant_id,
    replay_id,
    reason_code,
    observation_id
  );

create table public.replay_run_cohorts (
  tenant_id        uuid not null,
  replay_id        uuid not null,
  cohort_id        text not null,
  observation_count integer not null check (observation_count > 0),
  cohort           jsonb not null check (jsonb_typeof(cohort) = 'object'),
  primary key (tenant_id, replay_id, cohort_id),
  foreign key (tenant_id, replay_id)
    references public.replay_runs (tenant_id, replay_id) on delete cascade
);

create function public.enforce_replay_run_immutability() returns trigger
language plpgsql
set search_path = public
as $$
declare
  stored_lineage_count integer;
  stored_exclusion_count integer;
  stored_cohort_count integer;
  stored_cohort_observation_count integer;
  expected_coverage numeric;
  coverage_magnitude_gap integer;
  coverage_scale integer;
begin
  if old.tenant_id is distinct from new.tenant_id
     or old.replay_id is distinct from new.replay_id
     or old.replay_fingerprint is distinct from new.replay_fingerprint
     or old.input_sha256 is distinct from new.input_sha256
     or old.contract_version is distinct from new.contract_version
     or old.universe_ref is distinct from new.universe_ref
     or old.universe_id is distinct from new.universe_id
     or old.universe_sha256 is distinct from new.universe_sha256
     or old.comparison_rule is distinct from new.comparison_rule
     or old.expected_decision_count is distinct from new.expected_decision_count
     or old.request is distinct from new.request
     or old.advisory_only is distinct from new.advisory_only
     or old.submitted_by is distinct from new.submitted_by
     or old.created_at is distinct from new.created_at then
    raise exception 'replay run immutable input cannot be changed';
  end if;

  if old.status in ('completed', 'failed') then
    raise exception 'terminal replay run cannot be changed';
  end if;

  if not (
    new.status = old.status
    or (old.status = 'queued' and new.status in ('running', 'failed'))
    or (old.status = 'running' and new.status in ('queued', 'completed', 'failed'))
  ) then
    raise exception 'invalid replay run status transition: % -> %', old.status, new.status;
  end if;

  select count(*)
  into stored_lineage_count
  from public.replay_run_lineage
  where tenant_id = new.tenant_id and replay_id = new.replay_id;
  select count(*)
  into stored_exclusion_count
  from public.replay_run_exclusions
  where tenant_id = new.tenant_id and replay_id = new.replay_id;
  select count(*), coalesce(sum(observation_count), 0)
  into stored_cohort_count, stored_cohort_observation_count
  from public.replay_run_cohorts
  where tenant_id = new.tenant_id and replay_id = new.replay_id;

  if new.status = 'completed' then
    -- Python's replay contract computes Decimal ratios at 28 significant
    -- digits, not 28 fixed decimal places. Derive the leading-zero magnitude
    -- from the positive integer fraction, then round with excess input scale.
    if stored_lineage_count = 0 then
      expected_coverage := 0;
    else
      coverage_magnitude_gap :=
        length(new.expected_decision_count::text)
        - length(stored_lineage_count::text);
      if stored_lineage_count
           * power(10::numeric, coverage_magnitude_gap)
           < new.expected_decision_count then
        coverage_magnitude_gap := coverage_magnitude_gap + 1;
      end if;
      coverage_scale := 27 + coverage_magnitude_gap;
      expected_coverage := round(
        stored_lineage_count::numeric(1000, 100)
          / new.expected_decision_count::numeric,
        coverage_scale
      );
    end if;

    if stored_lineage_count + stored_exclusion_count
         <> new.expected_decision_count
       or stored_lineage_count
         <> coalesce((new.scorecard->>'observation_count')::integer, -1)
       or stored_lineage_count
         <> coalesce((new.scorecard->>'lineage_count')::integer, -1)
       or stored_exclusion_count
         <> coalesce(
              (new.scorecard->>'excluded_observation_count')::integer,
              -1
            )
       or new.expected_decision_count
         <> coalesce(
              (new.scorecard->>'total_observation_count')::integer,
              -1
            )
       or new.expected_decision_count
         <> coalesce(
              (new.scorecard->>'universe_decision_count')::integer,
              -1
            )
       or stored_cohort_count
         <> coalesce((new.scorecard->>'cohort_count')::integer, -1)
       or stored_cohort_observation_count <> stored_lineage_count
       or stored_lineage_count
         <> coalesce(
              (new.scorecard #>> '{current,decision_count}')::integer,
              -1
            )
       or stored_lineage_count
         <> coalesce(
              (new.scorecard #>> '{challenger,decision_count}')::integer,
              -1
            )
       or new.coverage_rate is distinct from expected_coverage
       or coalesce((new.scorecard->>'coverage_rate')::numeric, -1)
         <> expected_coverage
       or new.scorecard->>'contract_version' is distinct from 'replay.v1'
       or new.scorecard->>'tenant_id'
         is distinct from new.request->>'tenant_id'
       or new.scorecard->>'currency'
         is distinct from new.request->>'currency'
       or new.scorecard->>'universe_id' is distinct from new.universe_id
       or new.scorecard->>'universe_sha256'
         is distinct from new.universe_sha256
       or new.scorecard->>'comparison_rule'
         is distinct from new.comparison_rule
       or new.scorecard->>'advisory_only' is distinct from 'true'
       or new.detail #>> '{review_package,input_sha256}'
         is distinct from new.input_sha256
       or new.detail #>> '{review_package,universe_sha256}'
         is distinct from new.universe_sha256
       or new.detail #>> '{review_package,trusted_input_sha256}'
         is distinct from new.request->>'trusted_input_sha256'
       or coalesce(
            (new.detail #>> '{review_package,lineage_count}')::integer,
            -1
          ) <> stored_lineage_count
       or coalesce(
            (new.detail #>> '{review_package,exclusion_count}')::integer,
            -1
          ) <> stored_exclusion_count
       or coalesce(
            (new.detail #>> '{review_package,cohort_count}')::integer,
            -1
          ) <> stored_cohort_count then
      raise exception 'replay run evidence counts do not reconcile';
    end if;
  elsif new.status <> 'completed' and (
    stored_lineage_count <> 0
    or stored_exclusion_count <> 0
    or stored_cohort_count <> 0
  ) then
    raise exception 'non-completed replay run cannot retain evidence';
  end if;

  new.updated_at := now();
  return new;
end;
$$;

create trigger replay_runs_immutable
before update on public.replay_runs
for each row execute function public.enforce_replay_run_immutability();

revoke all on function public.enforce_replay_run_immutability()
  from public, anon, authenticated, service_role, trax_app, trax_seed;

create trigger replay_run_lineage_immutable
before update on public.replay_run_lineage
for each row execute function public.reject_planning_selection_update();
create trigger replay_run_exclusions_immutable
before update on public.replay_run_exclusions
for each row execute function public.reject_planning_selection_update();
create trigger replay_run_cohorts_immutable
before update on public.replay_run_cohorts
for each row execute function public.reject_planning_selection_update();
create trigger replay_universes_immutable
before update on public.replay_universes
for each row execute function public.reject_planning_selection_update();
create trigger replay_universe_rows_immutable
before update on public.replay_universe_rows
for each row execute function public.reject_planning_selection_update();

create function public.enforce_replay_universe_row_insert() returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  expected_rows integer;
begin
  select expected_decision_count
  into expected_rows
  from public.replay_universes
  where tenant_id = new.tenant_id and universe_ref = new.universe_ref
  for update;

  if not found then
    raise exception 'trusted replay universe is unavailable';
  end if;

  if new.ordinal >= expected_rows then
    raise exception 'sealed replay universe ordinal exceeds declared size';
  end if;
  return new;
end;
$$;

create trigger replay_universe_rows_sealed_insert
before insert on public.replay_universe_rows
for each row execute function public.enforce_replay_universe_row_insert();

create function public.enforce_replay_evidence_insert() returns trigger
language plpgsql
security definer
set search_path = pg_catalog, public
as $$
declare
  parent_status text;
begin
  select status
  into parent_status
  from public.replay_runs
  where tenant_id = new.tenant_id and replay_id = new.replay_id
  for update;

  if parent_status is distinct from 'running' then
    raise exception 'terminal replay evidence cannot be appended';
  end if;
  return new;
end;
$$;

create trigger replay_run_lineage_parent_running
before insert on public.replay_run_lineage
for each row execute function public.enforce_replay_evidence_insert();
create trigger replay_run_exclusions_parent_running
before insert on public.replay_run_exclusions
for each row execute function public.enforce_replay_evidence_insert();
create trigger replay_run_cohorts_parent_running
before insert on public.replay_run_cohorts
for each row execute function public.enforce_replay_evidence_insert();

revoke all on function public.enforce_replay_universe_row_insert()
  from public, anon, authenticated, service_role, trax_app, trax_seed;
revoke all on function public.enforce_replay_evidence_insert()
  from public, anon, authenticated, service_role, trax_app, trax_seed;

alter table public.replay_universes enable row level security;
alter table public.replay_universe_rows enable row level security;
alter table public.replay_runs enable row level security;
alter table public.replay_run_lineage enable row level security;
alter table public.replay_run_exclusions enable row level security;
alter table public.replay_run_cohorts enable row level security;

create policy replay_universes_select
  on public.replay_universes for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy replay_runs_select on public.replay_runs for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy replay_run_lineage_select
  on public.replay_run_lineage for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy replay_run_exclusions_select
  on public.replay_run_exclusions for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy replay_run_cohorts_select
  on public.replay_run_cohorts for select to trax_app
  using (tenant_id = (select public.current_tenant_id()));
create policy replay_runs_insert on public.replay_runs for insert to trax_app
  with check (
    tenant_id = (select public.current_tenant_id())
    and status = 'queued'
    and scorecard is null
    and finished_at is null
    and advisory_only
    and submitted_by = (
      select coalesce(
        nullif(current_setting('request.jwt.claims', true), ''),
        '{}'
      )::jsonb ->> 'sub'
    )
    and exists (
      select 1
      from public.replay_universes u
      where u.tenant_id = replay_runs.tenant_id
        and u.universe_ref = replay_runs.universe_ref
        and u.universe_id = replay_runs.universe_id
        and u.universe_sha256 = replay_runs.universe_sha256
        and u.expected_decision_count = replay_runs.expected_decision_count
    )
    and (
      select coalesce(
        nullif(current_setting('request.jwt.claims', true), ''),
        '{}'
      )::jsonb ->> 'tenant_role'
    ) in ('planner', 'admin', 'owner')
);

drop policy jobs_insert on public.jobs;
create policy jobs_insert on public.jobs for insert to trax_app
  with check (
    tenant_id = (select public.current_tenant_id())
    and (
      kind not in ('planning', 'replay')
      or (
        (
          select coalesce(
            nullif(current_setting('request.jwt.claims', true), ''),
            '{}'
          )::jsonb ->> 'tenant_role'
        ) in ('planner', 'admin', 'owner')
        and (
          (
            kind = 'planning'
            and exists (
              select 1
              from public.planning_runs r
              where r.tenant_id = jobs.tenant_id
                and r.run_id::text = jobs.payload->>'run_id'
                and r.status = 'queued'
            )
          )
          or (
            kind = 'replay'
            and exists (
              select 1
              from public.replay_runs r
              where r.tenant_id = jobs.tenant_id
                and r.replay_id::text = jobs.payload->>'replay_id'
                and r.status = 'queued'
            )
          )
        )
      )
    )
  );

-- Browser/BFF submissions resolve only bounded universe metadata. Historical
-- fact rows are intentionally unavailable to trax_app and are reconstructed
-- exclusively by the trax_seed worker after an asynchronous claim.
grant select on public.replay_universes to trax_app;
revoke all on public.replay_universe_rows from trax_app;
grant select, insert on public.replay_runs to trax_app;
grant select on public.replay_run_lineage, public.replay_run_exclusions,
  public.replay_run_cohorts to trax_app;
grant select, insert on public.replay_universes, public.replay_universe_rows
  to trax_seed;
grant select, insert, update on public.replay_runs to trax_seed;
grant select, insert on public.replay_run_lineage, public.replay_run_exclusions,
  public.replay_run_cohorts to trax_seed;
grant usage, select on all sequences in schema public to trax_app;
