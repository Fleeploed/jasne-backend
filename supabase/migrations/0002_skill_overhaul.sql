-- =====================================================================
-- Jasně — schema overhaul to match skill spec
--
-- Adds: multi-page scans, scan_messages (chat threads), usage_limits,
-- profile.plan, deadlines.auto_created. Run AFTER 0001_init.sql.
--
-- This drops & recreates `scans` and `deadlines` because we change
-- breaking columns (image_path -> image_paths text[]). Safe pre-launch
-- because there are no real users yet.
-- =====================================================================

-- ---------------------------------------------------------------------
-- profiles: add plan
-- ---------------------------------------------------------------------
alter table profiles
  add column if not exists plan text not null default 'free'
    check (plan in ('free', 'pro'));

create index if not exists profiles_plan_idx on profiles(plan);

-- ---------------------------------------------------------------------
-- scans: drop & recreate with image_paths array + page_count
-- ---------------------------------------------------------------------
drop table if exists deadlines cascade;       -- depends on scans
drop table if exists scan_messages cascade;   -- depends on scans
drop table if exists scans cascade;

create table scans (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles(id) on delete cascade,
  -- Storage paths in the `documents` bucket. Mobile uploads N pages,
  -- backend signs each URL and sends them all to GPT Vision in one call.
  image_paths text[] not null check (array_length(image_paths, 1) between 1 and 10),
  page_count int generated always as (coalesce(array_length(image_paths, 1), 0)) stored,
  ocr_text text,
  category text check (category in (
    'danove','socialni','zdravotni','cizinecka','smlouvy',
    'ucty','soudni','urad_prace','skolstvi','ostatni'
  )),
  doc_type text,
  category_confidence real,
  result jsonb,
  language text check (language in ('cs','en','uk','ru')),
  model_used text,
  created_at timestamptz not null default now()
);

create index scans_user_created_idx on scans(user_id, created_at desc);
create index scans_category_idx on scans(category);

-- ---------------------------------------------------------------------
-- scan_messages: chat thread for one scan (Pro feature)
--
-- First message (role=user, type=image)  — uploaded photo
-- Second message (role=assistant, type=result_card) — initial analysis
-- Following — Q&A pairs (role=user/assistant, type=text)
-- ---------------------------------------------------------------------
create table scan_messages (
  id uuid primary key default gen_random_uuid(),
  scan_id uuid not null references scans(id) on delete cascade,
  user_id uuid not null references profiles(id) on delete cascade,
  role text not null check (role in ('user', 'assistant')),
  content text not null,
  message_type text not null default 'text'
    check (message_type in ('text', 'image', 'result_card')),
  created_at timestamptz not null default now()
);

create index scan_messages_scan_created_idx
  on scan_messages(scan_id, created_at);

-- ---------------------------------------------------------------------
-- deadlines: recreate with auto_created flag
-- ---------------------------------------------------------------------
create table deadlines (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles(id) on delete cascade,
  scan_id uuid references scans(id) on delete set null,
  title text not null,
  description text,
  due_date date not null,
  reminder_days int[] not null default '{30,14,7,1}',
  deadline_type text not null default 'action'
    check (deadline_type in ('payment','action','response','expiration')),
  status text not null default 'active'
    check (status in ('active','completed','missed')),
  -- True when created by the analyzer (not by the user manually).
  -- Mobile UI uses this to show "Auto-created from scan" badge.
  auto_created boolean not null default false,
  created_at timestamptz not null default now()
);

create index deadlines_active_idx
  on deadlines(user_id, due_date) where status = 'active';

-- ---------------------------------------------------------------------
-- usage_limits: per-user, per-month counters for Free/Pro enforcement
--
-- Composite PK keeps history; cleaner than overwriting on month rollover.
-- questions_per_scan: { "<scan_uuid>": <count>, ... }
-- ---------------------------------------------------------------------
create table if not exists usage_limits (
  user_id uuid not null references profiles(id) on delete cascade,
  month text not null check (month ~ '^\d{4}-\d{2}$'),
  scans_count int not null default 0,
  questions_per_scan jsonb not null default '{}'::jsonb,
  primary key (user_id, month)
);

create index if not exists usage_limits_user_month_idx
  on usage_limits(user_id, month desc);

-- =====================================================================
-- Row Level Security
-- =====================================================================
alter table scans enable row level security;
alter table scan_messages enable row level security;
alter table deadlines enable row level security;
alter table usage_limits enable row level security;

-- scans
drop policy if exists "scans_select_own" on scans;
create policy "scans_select_own" on scans
  for select using (auth.uid() = user_id);

drop policy if exists "scans_insert_own" on scans;
create policy "scans_insert_own" on scans
  for insert with check (auth.uid() = user_id);

drop policy if exists "scans_delete_own" on scans;
create policy "scans_delete_own" on scans
  for delete using (auth.uid() = user_id);

-- scan_messages
drop policy if exists "scan_messages_select_own" on scan_messages;
create policy "scan_messages_select_own" on scan_messages
  for select using (auth.uid() = user_id);

drop policy if exists "scan_messages_insert_own" on scan_messages;
create policy "scan_messages_insert_own" on scan_messages
  for insert with check (auth.uid() = user_id);

-- deadlines
drop policy if exists "deadlines_all_own" on deadlines;
create policy "deadlines_all_own" on deadlines
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- usage_limits — read own, writes via service-role only
drop policy if exists "usage_limits_select_own" on usage_limits;
create policy "usage_limits_select_own" on usage_limits
  for select using (auth.uid() = user_id);
