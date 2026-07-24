-- supabase/migrations/20260723000010_billing_tenants.sql
-- C4: billing state on tenants + the DB-authoritative tier->quota map.

create type public.subscription_status as enum (
  'trialing','active','past_due','canceled',
  'incomplete','incomplete_expired','unpaid','paused'
);

alter table public.tenants
  add column stripe_customer_id     text unique,
  add column stripe_subscription_id text,
  add column subscription_status    public.subscription_status,
  add column current_period_end     timestamptz,
  add column trial_ends_at          timestamptz;

create table public.plan_tiers (
  tier         text primary key,
  key_quota    integer not null check (key_quota > 0),
  display_name text not null,
  sort         integer not null default 0
);
insert into public.plan_tiers (tier, key_quota, display_name, sort) values
  ('starter', 5000,   'Starter', 1),
  ('growth',  25000,  'Growth',  2),
  ('scale',   100000, 'Scale',   3);

alter table public.plan_tiers enable row level security;
-- Public read (the marketing pricing page is unauthenticated); no public writes.
create policy plan_tiers_public_read on public.plan_tiers
  for select to anon, authenticated using (true);
grant select on public.plan_tiers to anon, authenticated, trax_app;
-- NOTE: no update/insert/delete grant to anon/authenticated/trax_app → writes
-- require the service role (bypasses RLS), which no member path ever uses.
