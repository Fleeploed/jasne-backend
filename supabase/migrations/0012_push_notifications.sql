-- =====================================================================
-- Jasně — Expo push notifications
-- =====================================================================

create table if not exists push_tokens (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles(id) on delete cascade,
  expo_push_token text not null,
  platform text not null check (platform in ('ios', 'android')),
  timezone text,
  locale text,
  device_id text,
  app_version text,
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  unique(user_id, expo_push_token)
);

create index if not exists push_tokens_user_enabled_idx
  on push_tokens(user_id, enabled)
  where enabled = true;

create table if not exists notification_deliveries (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles(id) on delete cascade,
  deadline_id uuid not null references deadlines(id) on delete cascade,
  push_token_id uuid references push_tokens(id) on delete set null,
  reminder_day int not null check (reminder_day >= 0),
  scheduled_for date not null,
  sent_at timestamptz,
  status text not null default 'pending'
    check (status in ('pending', 'sent', 'error', 'receipt_ok')),
  expo_ticket_id text,
  expo_receipt_id text,
  error text,
  created_at timestamptz not null default now(),
  unique(deadline_id, reminder_day, push_token_id)
);

create index if not exists notification_deliveries_user_created_idx
  on notification_deliveries(user_id, created_at desc);

create index if not exists notification_deliveries_ticket_idx
  on notification_deliveries(expo_ticket_id)
  where expo_ticket_id is not null;

alter table push_tokens enable row level security;
alter table notification_deliveries enable row level security;

drop policy if exists push_tokens_select_own on push_tokens;
create policy push_tokens_select_own on push_tokens
  for select using (auth.uid() = user_id);

drop policy if exists push_tokens_insert_own on push_tokens;
create policy push_tokens_insert_own on push_tokens
  for insert with check (auth.uid() = user_id);

drop policy if exists push_tokens_update_own on push_tokens;
create policy push_tokens_update_own on push_tokens
  for update using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists push_tokens_delete_own on push_tokens;
create policy push_tokens_delete_own on push_tokens
  for delete using (auth.uid() = user_id);

drop policy if exists notification_deliveries_select_own on notification_deliveries;
create policy notification_deliveries_select_own on notification_deliveries
  for select using (auth.uid() = user_id);
