-- =====================================================================
-- Jasně — cascade-delete linked deadlines + bulk-delete user scans
--
-- 1. Switch deadlines.scan_id FK from ON DELETE SET NULL to ON DELETE
--    CASCADE so deleting a scan removes its auto-created deadlines.
-- 2. Add delete_user_scans(p_user_id) RPC that deletes all scans for a
--    user (cascading to scan_messages + linked deadlines) and resets the
--    user's current-month usage counters in a single transaction.
-- =====================================================================

-- 1) Promote deadlines.scan_id FK to ON DELETE CASCADE
alter table deadlines
  drop constraint if exists deadlines_scan_id_fkey;

alter table deadlines
  add constraint deadlines_scan_id_fkey
  foreign key (scan_id) references scans(id) on delete cascade;

-- 2) Bulk-delete RPC. Function bodies execute in a single transaction,
--    so either everything goes or nothing does.
create or replace function public.delete_user_scans(
  p_user_id uuid
)
returns table(deleted_scans int, deleted_deadlines int)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_month text := to_char(now() at time zone 'utc', 'YYYY-MM');
begin
  select count(*)::int into deleted_deadlines
  from deadlines
  where user_id = p_user_id and scan_id is not null;

  select count(*)::int into deleted_scans
  from scans
  where user_id = p_user_id;

  delete from scans where user_id = p_user_id;

  -- Reset current month's usage so the user can start over after a wipe.
  update usage_limits
     set scans_count = 0,
         questions_per_scan = '{}'::jsonb
   where user_id = p_user_id and month = v_month;

  return next;
end;
$$;
