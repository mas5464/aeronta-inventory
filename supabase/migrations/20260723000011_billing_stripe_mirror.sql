-- supabase/migrations/20260723000011_billing_stripe_mirror.sql
-- C4: Stripe mirror (tenant-billed). Written ONLY by the stripe-webhook Edge
-- Function (service role); everyone else reads.

create table public.products (
  id text primary key, active boolean not null default true,
  name text, description text, metadata jsonb not null default '{}'
);
create table public.prices (
  id text primary key,
  product_id text references public.products (id),
  active boolean not null default true,
  unit_amount bigint, currency text,
  interval text check (interval in ('day','week','month','year')),
  interval_count integer default 1,
  trial_period_days integer,
  metadata jsonb not null default '{}'   -- metadata.tier binds to plan_tiers
);
create table public.subscriptions (
  id text primary key,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  status public.subscription_status not null,
  price_id text references public.prices (id),
  quantity integer default 1,
  cancel_at_period_end boolean not null default false,
  current_period_end timestamptz,
  created timestamptz not null default now(),
  trial_end timestamptz
);
create index subscriptions_tenant_id_idx on public.subscriptions (tenant_id);
create table public.stripe_events (
  id text primary key, type text, received_at timestamptz not null default now()
);

alter table public.products enable row level security;
alter table public.prices enable row level security;
alter table public.subscriptions enable row level security;
alter table public.stripe_events enable row level security;

create policy products_public_read on public.products
  for select to anon, authenticated using (active);
create policy prices_public_read on public.prices
  for select to anon, authenticated using (active);
create policy subscriptions_tenant_read on public.subscriptions
  for select to trax_app, authenticated
  using (tenant_id = (select public.current_tenant_id()));

grant select on public.products, public.prices to anon, authenticated, trax_app;
grant select on public.subscriptions to authenticated, trax_app;
-- No write grants → mirror writes require the service role only. stripe_events
-- has no policy/grant at all → service-role-only (webhook idempotency ledger).
