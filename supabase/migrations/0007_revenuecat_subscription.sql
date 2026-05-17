-- =====================================================================
-- Jasně — RevenueCat subscription tracking
--
-- Adds: subscription details on profiles (product_id, expires_at,
-- will_renew) so the mobile profile screen can show "Pro until X • will
-- renew automatically". Also adds revenuecat_events table for webhook
-- deduplication and audit.
-- =====================================================================

-- ---------------------------------------------------------------------
-- profiles: subscription details mirrored from RevenueCat
-- ---------------------------------------------------------------------
alter table profiles
  add column if not exists subscription_product_id text,
  add column if not exists subscription_expires_at timestamptz,
  add column if not exists subscription_will_renew boolean not null default false;

-- Quick lookup for "users whose subscription expires soon" jobs
create index if not exists profiles_subscription_expires_at_idx
  on profiles(subscription_expires_at)
  where subscription_expires_at is not null;

-- ---------------------------------------------------------------------
-- revenuecat_events: webhook audit log + deduplication
--
-- RevenueCat delivers webhooks at-least-once. Primary key on event_id
-- gives us O(1) dedup: insert returns 0 rows on duplicate, we short-circuit.
-- raw_payload kept for debugging and possible replay.
-- ---------------------------------------------------------------------
create table if not exists revenuecat_events (
  event_id text primary key,
  event_type text not null,
  user_id uuid references profiles(id) on delete set null,
  app_user_id text,
  raw_payload jsonb not null,
  resulting_plan text check (resulting_plan in ('free', 'pro')),
  received_at timestamptz not null default now()
);

create index if not exists revenuecat_events_user_id_idx on revenuecat_events(user_id);
create index if not exists revenuecat_events_received_at_idx on revenuecat_events(received_at desc);

-- No RLS — this table is server-only (service key access).
-- Mobile clients never read or write it.
alter table revenuecat_events enable row level security;
-- Intentionally NO policies → all access denied for anon/auth roles.
-- Only service role bypasses RLS.
