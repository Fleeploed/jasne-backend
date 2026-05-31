-- =====================================================================
-- Jasně — OAMP status tracking for Pro users
-- =====================================================================

alter table profiles
  add column if not exists expo_push_token text;

create table if not exists tracked_applications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles(id) on delete cascade,
  cislo_jednaci text not null,
  current_status text not null check (
    current_status in ('probiha', 'schvaleno', 'zamitnuto', 'not_found', 'error')
  ),
  last_checked_at timestamptz,
  next_check_at timestamptz not null,
  is_active boolean not null default true,
  notification_count int not null default 0,
  consecutive_not_found_count int not null default 0,
  consecutive_error_count int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists tracked_applications_due_idx
  on tracked_applications(next_check_at)
  where is_active = true;

create index if not exists tracked_applications_user_active_idx
  on tracked_applications(user_id)
  where is_active = true;

create unique index if not exists tracked_applications_user_case_active_uidx
  on tracked_applications(user_id, cislo_jednaci)
  where is_active = true;

drop trigger if exists tracked_applications_touch on tracked_applications;
create trigger tracked_applications_touch
  before update on tracked_applications
  for each row execute function public.touch_updated_at();

alter table tracked_applications enable row level security;

drop policy if exists tracked_applications_select_own on tracked_applications;
create policy tracked_applications_select_own on tracked_applications
  for select using (auth.uid() = user_id);

drop policy if exists tracked_applications_update_own on tracked_applications;
create policy tracked_applications_update_own on tracked_applications
  for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
