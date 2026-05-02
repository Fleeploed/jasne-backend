-- Store the real page count after backend-side PDF rendering.
--
-- Before this migration page_count was a generated column based on the
-- number of storage paths. A single PDF can contain multiple pages, so the
-- backend now computes page_count explicitly.

alter table scans
  drop column if exists page_count;

alter table scans
  add column page_count int;

update scans
set page_count = coalesce(array_length(image_paths, 1), 0)
where page_count is null;

alter table scans
  alter column page_count set not null;

alter table scans
  add constraint scans_page_count_check check (page_count between 1 and 10);
