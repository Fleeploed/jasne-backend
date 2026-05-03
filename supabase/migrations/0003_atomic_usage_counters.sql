-- =====================================================================
-- Jasně — atomic usage counter increments
--
-- Prevents lost updates when multiple scan/chat requests for the same user
-- arrive concurrently.
-- =====================================================================

create or replace function public.increment_scan_count(
  p_user_id uuid,
  p_month text
)
returns int
language sql
security definer
set search_path = public
as $$
  insert into usage_limits (user_id, month, scans_count, questions_per_scan)
  values (p_user_id, p_month, 1, '{}'::jsonb)
  on conflict (user_id, month)
  do update set scans_count = usage_limits.scans_count + 1
  returning scans_count;
$$;

create or replace function public.increment_chat_count(
  p_user_id uuid,
  p_month text,
  p_scan_id uuid
)
returns int
language plpgsql
security definer
set search_path = public
as $$
declare
  scan_key text := p_scan_id::text;
  new_count int;
begin
  insert into usage_limits (user_id, month, scans_count, questions_per_scan)
  values (
    p_user_id,
    p_month,
    0,
    jsonb_build_object(scan_key, 1)
  )
  on conflict (user_id, month)
  do update set questions_per_scan = jsonb_set(
    coalesce(usage_limits.questions_per_scan, '{}'::jsonb),
    array[scan_key],
    to_jsonb(coalesce((usage_limits.questions_per_scan ->> scan_key)::int, 0) + 1),
    true
  )
  returning (questions_per_scan ->> scan_key)::int into new_count;

  return new_count;
end;
$$;
