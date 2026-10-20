-- =====================================================================
-- Jasně — account soft-delete / restore window
-- =====================================================================

alter table profiles
  add column if not exists deletion_status text not null default 'active',
  add column if not exists deletion_requested_at timestamptz,
  add column if not exists deletion_scheduled_at timestamptz,
  add column if not exists deletion_reason text,
  add column if not exists deletion_restored_at timestamptz;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'profiles_deletion_status_check'
      and conrelid = 'public.profiles'::regclass
  ) then
    alter table public.profiles
      add constraint profiles_deletion_status_check
      check (deletion_status in ('active', 'pending'));
  end if;
end $$;

create index if not exists profiles_pending_deletion_idx
  on profiles(deletion_scheduled_at)
  where deletion_status = 'pending';
