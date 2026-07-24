-- supabase/migrations/20260724000014_enqueue_due_recomputes.sql
-- C5: nightly per-tenant recompute enqueue.
--
-- Invoked by pg_cron (see deploy/C5_ROLLOUT.md) — NOT exposed through any HTTP
-- route, so no tenant can trigger another tenant's compute. The pg_cron
-- extension is deliberately NOT created here: it is unavailable on the
-- throwaway test container and would break the pg suite. This function is
-- pure SQL so the eligibility logic stays fully testable without it.
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
    select t.id as tenant_id,
           (select j.payload
              from public.jobs j
             where j.tenant_id = t.id and j.kind = 'ingest' and j.status = 'done'
             order by j.id desc
             limit 1) as last_payload
      from public.tenants t
     where t.subscription_status in ('trialing','active','past_due')
       -- must have something to replay
       and exists (select 1 from public.jobs j
                    where j.tenant_id = t.id and j.kind = 'ingest' and j.status = 'done')
       -- dedup: never stack work on a tenant that is already busy
       and not exists (select 1 from public.jobs j
                        where j.tenant_id = t.id
                          and j.kind in ('ingest','recompute')
                          and j.status in ('queued','running'))
  )
  insert into public.jobs (tenant_id, kind, payload, status)
  select tenant_id, 'recompute',
         last_payload || jsonb_build_object('source', 'recompute'),
         'queued'
    from eligible
   where last_payload is not null;

  get diagnostics v_count = row_count;
  return v_count;
end;
$$;

revoke execute on function public.enqueue_due_recomputes() from public;
-- Only the cron owner (postgres) and the worker role need this. Deliberately
-- NOT granted to authenticated/trax_app: no request path may trigger compute.
grant execute on function public.enqueue_due_recomputes() to trax_seed;
