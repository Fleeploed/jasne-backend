-- =====================================================================
-- Jasně — Pro usage limits follow the subscription period
--
-- usage_limits.month is a historical name. Free users still use YYYY-MM
-- buckets. Pro users now use subscription-period keys such as:
--   pro:2026-05-22:2026-06-22
-- =====================================================================

alter table usage_limits
  drop constraint if exists usage_limits_month_check;

alter table usage_limits
  add constraint usage_limits_month_check
  check (
    month ~ '^\d{4}-\d{2}$'
    or month ~ '^pro:\d{4}-\d{2}-\d{2}:\d{4}-\d{2}-\d{2}$'
  );

