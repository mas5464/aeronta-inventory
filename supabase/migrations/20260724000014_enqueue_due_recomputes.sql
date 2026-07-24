-- supabase/migrations/20260724000014_enqueue_due_recomputes.sql
-- C5: nightly per-tenant recompute enqueue.
--
-- Invoked by pg_cron (see deploy/C5_ROLLOUT.md) — NOT exposed through any HTTP
-- route, so no tenant can trigger another tenant's compute. The pg_cron
-- extension is deliberately NOT created here: it is unavailable on the
-- throwaway test container and would break the pg suite. This function is
-- pure SQL so the eligibility logic stays fully testable without it.
--
-- Invariant (adjudicated fix — do not reintroduce a payload snapshot here):
-- the enqueued job's payload deliberately carries NO data snapshot. The
-- dedup check below is a non-atomic check-then-insert under READ COMMITTED,
-- so a cron tick and a concurrent user `POST …/ingest` can both commit
-- without either seeing the other. If this function instead captured "the
-- tenant's latest done-ingest payload" at enqueue time, the losing race
-- would freeze a stale payload into the recompute job; the worker drains
-- jobs in id order, so it would replay that stale payload *after* the
-- user's fresh upload — silently reverting it. Instead the recompute
-- handler resolves the tenant's latest status='done' ingest payload itself
-- when the job actually runs, so a race can only ever cause a recompute to
-- replay data that is newer than what was known at enqueue time, never
-- older.
create function public.enqueue_due_recomputes()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_count integer := 0;
begin
  with eligible as (
    select t.id as tenant_id
      from public.tenants t
     where t.subscription_status in ('trialing','active','past_due')
       -- must have something to replay — this is now the SOLE "has work"
       -- guard (the payload itself is resolved at run time, not captured
       -- here; see the invariant note above)
       and exists (select 1 from public.jobs j
                    where j.tenant_id = t.id and j.kind = 'ingest' and j.status = 'done')
       -- dedup: never stack work on a tenant that is already busy. This is
       -- a best-effort efficiency guard, not a correctness guarantee — see
       -- the invariant note above for why correctness does not depend on
       -- it being atomic.
       and not exists (select 1 from public.jobs j
                        where j.tenant_id = t.id
                          and j.kind in ('ingest','recompute')
                          and j.status in ('queued','running'))
  )
  insert into public.jobs (tenant_id, kind, payload, status)
  select tenant_id, 'recompute',
         jsonb_build_object('source', 'recompute'),
         'queued'
    from eligible;

  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke execute on function public.enqueue_due_recomputes() from public;
-- Only the cron owner (postgres) and the worker role need this. Deliberately
-- NOT granted to authenticated/trax_app: no request path may trigger compute.
grant execute on function public.enqueue_due_recomputes() to trax_seed;
