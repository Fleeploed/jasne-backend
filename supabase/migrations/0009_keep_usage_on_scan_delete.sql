-- =====================================================================
-- Jasně — bulk-delete must not reset monthly usage counters
--
-- The previous version of delete_user_scans (0008) zeroed the user's
-- current-month usage_limits row. That lets a Free user scan up to their
-- monthly cap, wipe their documents, and scan again — bypassing the
-- limit indefinitely. usage_limits.scans_count tracks scans *initiated*
-- this month and only resets on natural monthly rollover (new month
-- key), never on deletion.
--
-- This migration replaces the function with one that still cascades
-- scans -> scan_messages + deadlines via FK ON DELETE CASCADE but does
-- not touch usage_limits.
-- =====================================================================

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
  where user_id = p_user_id and scan_id is not null;

  select count(*)::int into deleted_scans
  from scans
  where user_id = p_user_id;

  delete from scans where user_id = p_user_id;

  return next;
end;
$$;
