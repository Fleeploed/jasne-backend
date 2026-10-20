-- =====================================================================
-- Jasně — manual deadlines must survive scan deletion.
--
-- Migration 0008 switched deadlines.scan_id to ON DELETE CASCADE, which
-- silently destroys reminders the user created by hand and merely linked
-- to a scan. Split the behavior by origin:
--   * auto_created = true  → deleted with the scan (BEFORE DELETE trigger)
--   * auto_created = false → unlinked (FK back to ON DELETE SET NULL)
-- =====================================================================

-- 1) FK back to SET NULL — the trigger below handles auto-created rows.
alter table deadlines
  drop constraint if exists deadlines_scan_id_fkey;

alter table deadlines
  add constraint deadlines_scan_id_fkey
  foreign key (scan_id) references scans(id) on delete set null;

-- 2) Per-row trigger: remove auto-created deadlines before their scan goes.
--    Fires for single-scan deletes, bulk deletes, and delete_user_scans alike.
create or replace function public.delete_auto_deadlines_for_scan()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  delete from deadlines where scan_id = old.id and auto_created;
  return old;
end;
$$;

drop trigger if exists scans_delete_auto_deadlines on scans;
create trigger scans_delete_auto_deadlines
  before delete on scans
  for each row execute function public.delete_auto_deadlines_for_scan();

-- 3) delete_user_scans: count only the deadlines that will actually be
--    deleted (auto-created linked ones). Based on the 0009 version — usage
--    counters stay untouched (they track initiations, not surviving rows).
create or replace function public.delete_user_scans(
  p_user_id uuid
)
returns table(deleted_scans int, deleted_deadlines int)
language plpgsql
security definer
set search_path = public
as $$
begin
  select count(*)::int into deleted_deadlines
  from deadlines
  where user_id = p_user_id and scan_id is not null and auto_created;

  select count(*)::int into deleted_scans
  from scans
  where user_id = p_user_id;

  delete from scans where user_id = p_user_id;

  return next;
end;
$$;
