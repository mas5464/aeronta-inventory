-- C1: Supabase custom access token hook (spec §3 — claims in token, app_metadata-grade).
-- Registered in the Supabase dashboard/config as the access-token hook in C2 deploy;
-- pure SQL so it is testable on plain Postgres today.
create function public.custom_access_token_hook(event jsonb) returns jsonb
language plpgsql stable as $$
declare
  uid uuid := (event->>'user_id')::uuid;
  requested uuid := nullif(event->'claims'->>'tenant_id', '')::uuid;
  m record;
begin
  select tenant_id, role into m
  from public.memberships
  where user_id = uid
    and (requested is null or tenant_id = requested)
  order by (tenant_id = requested) desc nulls last, created_at desc
  limit 1;

  -- if requested was provided but user is not a member of it, fall back to most recent
  if m is null and requested is not null then
    select tenant_id, role into m
    from public.memberships
    where user_id = uid
    order by created_at desc
    limit 1;
  end if;

  if m is null then
    -- no membership: strip any tenant claims rather than passing through junk
    return jsonb_set(
      event, '{claims}',
      (event->'claims') - 'tenant_id' - 'tenant_role'
    );
  end if;

  return jsonb_set(
    event, '{claims}',
    (event->'claims')
      || jsonb_build_object('tenant_id', m.tenant_id::text, 'tenant_role', m.role)
  );
end;
$$;

-- Supabase runs hooks as supabase_auth_admin; on the shim, trax_seed suffices.
grant execute on function public.custom_access_token_hook(jsonb) to trax_seed;
