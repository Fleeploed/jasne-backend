-- =====================================================================
-- Jasně — Help section moves to Postgres (admin-managed via web CMS)
--
-- Supersedes the 2026-05-16 "YAML in git, no admin API" decision for the
-- Help section. Categories and guides now live in two tables that the
-- single-file admin (help-admin/index.html) writes through the Supabase
-- anon key + RLS.
--
-- IMPORTANT compatibility note:
--   help_categories holds the editable category basics (slug, localized
--   title, icon, sort_order). The rich UI metadata that the mobile app
--   response models still need — hero block, fallback block, useful_actions
--   override, guides_order — continues to live in help_content/categories.yaml.
--   The admin does not edit that metadata; it's stable and changes rarely.
-- =====================================================================

-- ---------------------------------------------------------------------
-- profiles.role — promote yourself to admin via the bootstrap comment
-- at the bottom of this file.
-- ---------------------------------------------------------------------
alter table profiles
  add column if not exists role text not null default 'user'
  check (role in ('user', 'admin'));

-- ---------------------------------------------------------------------
-- help_categories
-- ---------------------------------------------------------------------
create table if not exists help_categories (
  slug         text primary key,
  -- Localized titles: { "cs": "...", "en": "...", "uk": "...", "ru": "..." }
  -- 'cs' is the canonical fallback and must be present (enforced in app code).
  title        jsonb not null default '{}'::jsonb,
  icon         text,
  sort_order   integer not null default 0,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create index if not exists help_categories_sort_order_idx
  on help_categories (sort_order, slug);

-- ---------------------------------------------------------------------
-- help_guides
--
-- sections jsonb layout (mirrors app.models.help_schemas.HelpSection):
--   text:      { "type": "text",      "title": str|null, "body": str }
--   steps:     { "type": "steps",     "title": str|null, "items": [str, ...] }
--   checklist: { "type": "checklist", "title": str|null, "items": [str, ...] }
--   warning:   { "type": "warning",   "title": str|null, "body": str }
--   cta:       { "type": "cta",       "title": str|null, "actions": [str, ...] }
--     action keys reference help_ui_strings.useful_actions (e.g.
--     "scan_oamp_letter", "add_deadline_manual", "open_deadlines").
--
-- NOTE: this differs from the user-supplied spec on two points and
-- intentionally so — the mobile app already consumes this exact shape:
--   - section heading field is named "title", not "heading"
--   - cta uses "actions: [str]" keyed by ui-string slug, not "label/action"
-- ---------------------------------------------------------------------
create table if not exists help_guides (
  id                  uuid primary key default gen_random_uuid(),
  slug                text not null,
  category_slug       text not null
                       references help_categories(slug)
                       on delete restrict,
  language            text not null check (language in ('cs', 'en', 'uk', 'ru')),
  status              text not null default 'draft'
                       check (status in ('draft', 'published')),
  title               text not null,
  subtitle            text,
  short_summary       text,
  keywords            text[] not null default '{}'::text[],
  official_source_url text,
  sections            jsonb not null default '[]'::jsonb,
  sort_order          integer not null default 0,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (slug, language)
);

create index if not exists help_guides_category_idx
  on help_guides (category_slug);
create index if not exists help_guides_status_lang_idx
  on help_guides (status, language);
create index if not exists help_guides_sections_gin
  on help_guides using gin (sections);
create index if not exists help_guides_keywords_gin
  on help_guides using gin (keywords);

-- ---------------------------------------------------------------------
-- updated_at touch trigger (shared)
-- ---------------------------------------------------------------------
create or replace function help_touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists help_categories_touch_updated on help_categories;
create trigger help_categories_touch_updated
  before update on help_categories
  for each row execute function help_touch_updated_at();

drop trigger if exists help_guides_touch_updated on help_guides;
create trigger help_guides_touch_updated
  before update on help_guides
  for each row execute function help_touch_updated_at();

-- ---------------------------------------------------------------------
-- Row Level Security
--
-- We use one permissive SELECT policy per table with an inline admin check
-- rather than two policies. Reason: a single policy with `OR <admin>` is
-- easier to reason about than two policies that postgres ORs together,
-- and we don't pay an extra subplan when an anonymous client hits the
-- published-only branch.
-- ---------------------------------------------------------------------
alter table help_categories enable row level security;
alter table help_guides enable row level security;

-- helper: is the current JWT subject a profile with role='admin'?
create or replace function is_help_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from profiles p
    where p.id = auth.uid() and p.role = 'admin'
  );
$$;

-- help_categories: public read; admin write.
drop policy if exists help_categories_read on help_categories;
create policy help_categories_read on help_categories
  for select
  to anon, authenticated
  using (true);

drop policy if exists help_categories_admin_write on help_categories;
create policy help_categories_admin_write on help_categories
  for all
  to authenticated
  using (is_help_admin())
  with check (is_help_admin());

-- help_guides: public reads see published only; admins see everything.
drop policy if exists help_guides_read on help_guides;
create policy help_guides_read on help_guides
  for select
  to anon, authenticated
  using (status = 'published' or is_help_admin());

drop policy if exists help_guides_admin_write on help_guides;
create policy help_guides_admin_write on help_guides
  for all
  to authenticated
  using (is_help_admin())
  with check (is_help_admin());

-- ---------------------------------------------------------------------
-- Seed: the 6 MVP categories. Titles and icons mirror
-- help_content/categories.yaml. Idempotent — re-running this migration
-- in a fresh DB rebuilds the seed.
-- ---------------------------------------------------------------------
insert into help_categories (slug, title, icon, sort_order) values
  ('visa-pobyt', jsonb_build_object(
      'cs', 'Víza a pobytová oprávnění',
      'ru', 'Визы и разрешения на проживание',
      'uk', 'Візи та дозволи на проживання',
      'en', 'Visas and residence permits'
    ), 'passport', 10),
  ('taxes', jsonb_build_object(
      'cs', 'Daně',
      'ru', 'Налоги',
      'uk', 'Податки',
      'en', 'Taxes'
    ), 'receipt', 20),
  ('bills', jsonb_build_object(
      'cs', 'Účty a faktury',
      'ru', 'Счета и платежи',
      'uk', 'Рахунки та платежі',
      'en', 'Bills and payments'
    ), 'zap', 30),
  ('contracts', jsonb_build_object(
      'cs', 'Smlouvy',
      'ru', 'Договоры',
      'uk', 'Договори',
      'en', 'Contracts'
    ), 'doc', 40),
  ('urad-prace', jsonb_build_object(
      'cs', 'Úřad práce',
      'ru', 'Биржа труда',
      'uk', 'Бюро праці',
      'en', 'Labour Office'
    ), 'briefcase', 50),
  ('health-insurance', jsonb_build_object(
      'cs', 'Zdravotní pojištění',
      'ru', 'Медстраховка',
      'uk', 'Медичне страхування',
      'en', 'Health insurance'
    ), 'shield', 60)
on conflict (slug) do nothing;

-- ---------------------------------------------------------------------
-- Bootstrap: promote yourself to admin.
-- Find your auth uid in Supabase Dashboard → Authentication → Users
-- (the "User UID" column), then run, e.g.:
--
--   update profiles
--   set role = 'admin'
--   where id = '00000000-0000-0000-0000-000000000000';
--
-- Existing guides authored as YAML can be imported with
-- scripts/seed_help_guides_from_yaml.py — see help-admin/README.md.
-- ---------------------------------------------------------------------
