-- =====================================================================
-- Jasně — initial schema
-- =====================================================================

create extension if not exists vector;
create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------
-- profiles
-- ---------------------------------------------------------------------
create table if not exists profiles (
  id uuid references auth.users on delete cascade primary key,
  display_name text,
  language text not null default 'cs' check (language in ('cs','en','uk','ru')),
  has_datova_schranka boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Auto-create profile row on auth signup
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data->>'display_name', null))
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Keep updated_at fresh
create or replace function public.touch_updated_at()
returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end;
$$;

drop trigger if exists profiles_touch on profiles;
create trigger profiles_touch
  before update on profiles
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------
-- scans
-- ---------------------------------------------------------------------
create table if not exists scans (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles(id) on delete cascade,
  image_path text not null,
  ocr_text text,
  category text check (category in (
    'danove','socialni','zdravotni','cizinecka','smlouvy',
    'ucty','soudni','urad_prace','skolstvi','ostatni'
  )),
  doc_type text,
  category_confidence real,
  result jsonb,
  language text check (language in ('cs','en','uk','ru')),
  model_used text,
  created_at timestamptz not null default now()
);

create index if not exists scans_user_created_idx on scans(user_id, created_at desc);
create index if not exists scans_category_idx on scans(category);

-- ---------------------------------------------------------------------
-- deadlines
-- ---------------------------------------------------------------------
create table if not exists deadlines (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles(id) on delete cascade,
  scan_id uuid references scans(id) on delete set null,
  title text not null,
  description text,
  due_date date not null,
  reminder_days int[] not null default '{30,14,7,1}',
  deadline_type text not null default 'action'
    check (deadline_type in ('payment','action','response','expiration')),
  status text not null default 'active'
    check (status in ('active','completed','missed')),
  created_at timestamptz not null default now()
);

create index if not exists deadlines_active_idx
  on deadlines(user_id, due_date) where status = 'active';

-- ---------------------------------------------------------------------
-- knowledge_chunks (RAG)
-- ---------------------------------------------------------------------
create table if not exists knowledge_chunks (
  id bigserial primary key,
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  embedding vector(1536)
);

create index if not exists knowledge_chunks_embedding_idx
  on knowledge_chunks using ivfflat (embedding vector_cosine_ops) with (lists = 100);

create index if not exists knowledge_chunks_category_idx
  on knowledge_chunks using gin ((metadata->'category'));

-- ---------------------------------------------------------------------
-- RPC: match_knowledge_chunks — cosine-similarity search with category filter
-- ---------------------------------------------------------------------
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

-- =====================================================================
-- Row Level Security
-- =====================================================================
alter table profiles enable row level security;
alter table scans enable row level security;
alter table deadlines enable row level security;
alter table knowledge_chunks enable row level security;

-- profiles
drop policy if exists "profiles_select_own" on profiles;
create policy "profiles_select_own" on profiles
  for select using (auth.uid() = id);

drop policy if exists "profiles_update_own" on profiles;
create policy "profiles_update_own" on profiles
  for update using (auth.uid() = id);

-- scans
drop policy if exists "scans_select_own" on scans;
create policy "scans_select_own" on scans
  for select using (auth.uid() = user_id);

drop policy if exists "scans_insert_own" on scans;
create policy "scans_insert_own" on scans
  for insert with check (auth.uid() = user_id);

-- deadlines
drop policy if exists "deadlines_all_own" on deadlines;
create policy "deadlines_all_own" on deadlines
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- knowledge_chunks — read-only for all authenticated
drop policy if exists "knowledge_read_authenticated" on knowledge_chunks;
create policy "knowledge_read_authenticated" on knowledge_chunks
  for select using (auth.role() = 'authenticated');

-- =====================================================================
-- Storage bucket: documents (private)
-- =====================================================================
insert into storage.buckets (id, name, public)
values ('documents', 'documents', false)
on conflict (id) do nothing;

drop policy if exists "documents_owner_read" on storage.objects;
create policy "documents_owner_read" on storage.objects
  for select using (
    bucket_id = 'documents'
    and auth.uid()::text = (storage.foldername(name))[1]
  );

drop policy if exists "documents_owner_insert" on storage.objects;
create policy "documents_owner_insert" on storage.objects
  for insert with check (
    bucket_id = 'documents'
    and auth.uid()::text = (storage.foldername(name))[1]
  );

drop policy if exists "documents_owner_delete" on storage.objects;
create policy "documents_owner_delete" on storage.objects
  for delete using (
    bucket_id = 'documents'
    and auth.uid()::text = (storage.foldername(name))[1]
  );
