-- =====================================================================
-- Jasně — generic background tracking infrastructure
-- =====================================================================

alter table profiles
  add column if not exists expo_push_token text;

do $$
begin
  if to_regclass('public.trackings') is null
     and to_regclass('public.tracked_applications') is not null then
    alter table public.tracked_applications rename to trackings;
  end if;
end $$;

create table if not exists trackings (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles(id) on delete cascade,
  tracking_type text not null,
  identifier text not null,
  current_status text,
  metadata jsonb not null default '{}'::jsonb,
  last_checked_at timestamptz,
  next_check_at timestamptz not null,
  is_active boolean not null default true,
  notification_count int not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'trackings'
      and column_name = 'cislo_jednaci'
  ) and not exists (
    select 1 from information_schema.columns
    where table_schema = 'public'
      and table_name = 'trackings'
      and column_name = 'identifier'
  ) then
    alter table public.trackings rename column cislo_jednaci to identifier;
  end if;
end $$;

alter table trackings
  add column if not exists tracking_type text,
  add column if not exists metadata jsonb not null default '{}'::jsonb;

update trackings
set tracking_type = coalesce(nullif(tracking_type, ''), 'oamp_status')
where tracking_type is null or tracking_type = '';

alter table trackings
  alter column tracking_type set not null,
  alter column identifier set not null,
  alter column metadata set default '{}'::jsonb,
  alter column metadata set not null;

alter table trackings
  drop constraint if exists tracked_applications_current_status_check,
  drop constraint if exists trackings_current_status_check;

drop index if exists tracked_applications_due_idx;
drop index if exists tracked_applications_user_active_idx;
drop index if exists tracked_applications_user_case_active_uidx;

create index if not exists trackings_due_idx
  on trackings(next_check_at)
  where is_active = true;

create index if not exists trackings_user_active_idx
  on trackings(user_id)
  where is_active = true;

create unique index if not exists trackings_user_type_identifier_active_uidx
  on trackings(user_id, tracking_type, identifier)
  where is_active = true;

drop trigger if exists tracked_applications_touch on trackings;
drop trigger if exists trackings_touch on trackings;
create trigger trackings_touch
  before update on trackings
  for each row execute function public.touch_updated_at();

alter table trackings enable row level security;

drop policy if exists tracked_applications_select_own on trackings;
drop policy if exists tracked_applications_update_own on trackings;
drop policy if exists trackings_select_own on trackings;
create policy trackings_select_own on trackings
  for select using (auth.uid() = user_id);

drop policy if exists trackings_update_own on trackings;
create policy trackings_update_own on trackings
  for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
