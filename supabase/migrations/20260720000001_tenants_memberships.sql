-- C1: tenancy core. Spec §3.
create extension if not exists pgcrypto;

create function public.current_tenant_id() returns uuid
language sql stable as $$
  select nullif(auth.jwt()->>'tenant_id', '')::uuid
$$;

create table public.tenants (
  id         uuid primary key default gen_random_uuid(),
  slug       text not null unique check (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
  name       text not null,
  plan_tier  text not null default 'trial'
             check (plan_tier in ('trial', 'starter', 'growth', 'scale', 'enterprise')),
  key_quota  integer not null default 5000 check (key_quota > 0),
  created_at timestamptz not null default now()
);

create table public.memberships (
  user_id    uuid not null,
  tenant_id  uuid not null references public.tenants (id) on delete cascade,
  role       text not null check (role in ('owner', 'admin', 'planner', 'viewer')),
  created_at timestamptz not null default now(),
  primary key (user_id, tenant_id)
);
create index memberships_tenant_id_idx on public.memberships (tenant_id);

alter table public.tenants enable row level security;
alter table public.memberships enable row level security;

-- A member sees their own tenant row; only the seed/admin path creates tenants.
create policy tenants_select on public.tenants for select to trax_app, authenticated
  using (id = (select public.current_tenant_id()));

-- A member sees the member list of their active tenant.
create policy memberships_select on public.memberships for select to trax_app, authenticated
  using (tenant_id = (select public.current_tenant_id()));

grant usage on schema public to trax_app, trax_seed;
grant select on public.tenants, public.memberships to trax_app;
grant all on public.tenants, public.memberships to trax_seed;
