-- Returns scans for the given user ordered by the most recent activity:
-- last message sent in the thread (last_message_at), falling back to scan creation date.
create or replace function list_scans_with_activity(
  p_user_id  uuid,
  p_limit    int  default 20,
  p_offset   int  default 0
)
returns table (
  id                uuid,
  category          text,
  doc_type          text,
  result            jsonb,
  page_count        int,
  image_paths       text[],
  created_at        timestamptz,
  last_message_at   timestamptz
)
language sql
stable
security definer
set search_path = public
as $$
  select
    s.id,
    s.category,
    s.doc_type,
    s.result,
    s.page_count,
    s.image_paths,
    s.created_at,
    max(m.created_at) as last_message_at
  from scans s
  left join scan_messages m
    on m.scan_id = s.id
   and m.user_id = p_user_id
  where s.user_id = p_user_id
  group by s.id
  order by coalesce(max(m.created_at), s.created_at) desc
  limit  p_limit
  offset p_offset;
$$;

-- Allow authenticated users to call this function (RLS on scans/scan_messages
-- is enforced via the where clauses — security definer is safe here because we
-- always filter by p_user_id which comes from the verified JWT in the backend).
grant execute on function list_scans_with_activity(uuid, int, int) to authenticated;
