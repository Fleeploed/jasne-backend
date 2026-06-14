-- =====================================================================
-- Jasně — close the check-then-increment quota race.
--
-- check_scan_quota / check_chat_quota read the counter, and the increment
-- happens later as a separate RPC. N parallel requests at cap-1 all pass
-- the read, then all increment — blowing past the cap. These RPCs combine
-- the check and the increment in one conditional UPDATE: the WHERE clause
-- is re-evaluated under the row lock, so at most `cap` increments ever
-- succeed. Returns the new count, or -1 when the cap is reached.
-- =====================================================================

create or replace function public.try_increment_scan_count(
  p_user_id uuid,
  p_month text,
  p_cap int
)
returns int
language plpgsql
security definer
set search_path = public
as $$
declare
  v_new int;
begin
  insert into usage_limits (user_id, month, scans_count, questions_per_scan)
  values (p_user_id, p_month, 0, '{}'::jsonb)
  on conflict (user_id, month) do nothing;

  update usage_limits
     set scans_count = scans_count + 1
   where user_id = p_user_id
     and month = p_month
     and scans_count < p_cap
  returning scans_count into v_new;

  return coalesce(v_new, -1);
end;
$$;

create or replace function public.try_increment_chat_count(
  p_user_id uuid,
  p_month text,
  p_scan_id uuid,
  p_cap int
)
returns int
language plpgsql
security definer
set search_path = public
as $$
declare
  v_key text := p_scan_id::text;
  v_new int;
begin
  insert into usage_limits (user_id, month, scans_count, questions_per_scan)
  values (p_user_id, p_month, 0, '{}'::jsonb)
  on conflict (user_id, month) do nothing;

  update usage_limits
     set questions_per_scan = jsonb_set(
           coalesce(questions_per_scan, '{}'::jsonb),
           array[v_key],
           to_jsonb(coalesce((questions_per_scan ->> v_key)::int, 0) + 1)
         )
   where user_id = p_user_id
     and month = p_month
     and coalesce((questions_per_scan ->> v_key)::int, 0) < p_cap
  returning (questions_per_scan ->> v_key)::int into v_new;

  return coalesce(v_new, -1);
end;
$$;
