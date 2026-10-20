-- Aggregate scan counts per category in the database instead of pulling up to
-- 10k rows into the backend and counting them in Python (see GET /scans/counts).
create or replace function count_scans_by_category(
  p_user_id uuid
)
returns table (
  category text,
  count    bigint
)
language sql
stable
security definer
set search_path = public
as $$
  select
    coalesce(s.category, 'ostatni') as category,
    count(*)                        as count
  from scans s
  where s.user_id = p_user_id
  group by coalesce(s.category, 'ostatni');
$$;

-- security definer is safe: results are always scoped to p_user_id, which the
-- backend derives from the verified JWT.
grant execute on function count_scans_by_category(uuid) to authenticated;
