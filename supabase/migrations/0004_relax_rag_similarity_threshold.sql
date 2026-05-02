-- Relax default RAG threshold for existing databases.
--
-- The backend passes an explicit threshold from app settings, but the RPC
-- default should match the production-safe value for manual checks and
-- direct Supabase RPC usage.
create or replace function public.match_knowledge_chunks(
  query_embedding vector(1536),
  filter_category text,
  match_threshold float default 0.50,
  match_count int default 5
)
returns table (
  id bigint,
  content text,
  metadata jsonb,
  similarity float
)
language sql
stable
as $$
  select
    kc.id,
    kc.content,
    kc.metadata,
    1 - (kc.embedding <=> query_embedding) as similarity
  from knowledge_chunks kc
  where kc.metadata->>'category' = filter_category
    and 1 - (kc.embedding <=> query_embedding) > match_threshold
  order by kc.embedding <=> query_embedding
  limit match_count;
$$;
