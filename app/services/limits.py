"""Usage limits enforcement — Free vs Pro plans.

Skill caps:
  Free → 5 scans/month, 3 pages/scan, 0 chat questions
  Pro  → 100 scans/month, 10 pages/scan, 10 chat questions per scan

Free counters reset by calendar month. Pro counters reset by subscription
period, so a user buying on May 22 gets a Pro quota bucket from May 22 to
June 22 instead of losing quota at the end of May.

Counters live in `usage_limits` (composite PK on user_id+month). The column is
historically named `month`; for Pro it stores a stable subscription period key
such as `pro:2026-05-22:2026-06-22`.
"""

from __future__ import annotations

import calendar
import logging
from datetime import UTC, datetime
from typing import TypedDict
from uuid import UUID

from app.models.schemas import UserPlan
from app.services.rag import get_supabase

logger = logging.getLogger(__name__)


class QuotaExceededError(Exception):
    """Plan quota exhausted. Routers map this to 402.

    Deliberately NOT a ValueError: the global ValueError handler returns a
    generic message, while quota messages are intentionally user-facing.
    """


class PlanCaps(TypedDict):
    monthly_scans: int
    max_pages_per_scan: int
    max_questions_per_scan: int
    # How many most-recent scans the archive shows. None = unlimited (Pro).
    archive_limit: int | None


PLAN_LIMITS: dict[UserPlan, PlanCaps] = {
    "free": {
        "monthly_scans": 5,
        "max_pages_per_scan": 3,
        "max_questions_per_scan": 0,
        "archive_limit": 5,
    },
    "pro": {
        "monthly_scans": 100,
        "max_pages_per_scan": 10,
        "max_questions_per_scan": 10,
        "archive_limit": None,
    },
}


def caps_for_plan(plan: UserPlan) -> PlanCaps:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])


def current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _parse_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Could not parse subscription_expires_at for usage period: %s", value)
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _subtract_calendar_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 - months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def usage_period_for_plan(
    plan: UserPlan,
    *,
    subscription_expires_at: datetime | str | None = None,
    subscription_product_id: str | None = None,
) -> str:
    """Return the usage bucket key for the current request.

    Free remains calendar-month based. Pro follows the active billing period
    derived from RevenueCat's entitlement expiration. Monthly products subtract
    one calendar month from `expires_at`; yearly products subtract one year.
    """
    if plan != "pro":
        return current_month()

    expires_at = _parse_datetime(subscription_expires_at)
    if expires_at is None or expires_at <= datetime.now(UTC):
        return current_month()

    product = (subscription_product_id or "").lower()
    period_months = 12 if "year" in product or "annual" in product else 1
    starts_at = _subtract_calendar_months(expires_at, period_months)
    return f"pro:{starts_at.date().isoformat()}:{expires_at.date().isoformat()}"


def usage_period_for_profile(profile: dict) -> str:
    return usage_period_for_plan(
        profile.get("plan", "free"),
        subscription_expires_at=profile.get("subscription_expires_at"),
        subscription_product_id=profile.get("subscription_product_id"),
    )


async def _read_row(user_id: UUID, month: str) -> dict | None:
    supabase = await get_supabase()
    result = (
        await supabase.table("usage_limits")
        .select("*")
        .eq("user_id", str(user_id))
        .eq("month", month)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


async def get_or_init_usage(user_id: UUID, month: str | None = None) -> dict:
    """Read the current usage period row, creating it if missing.

    Uses upsert with ignore_duplicates to avoid PK-violation races when two
    requests arrive before the period row exists.
    """
    month = month or current_month()
    row = await _read_row(user_id, month)
    if row is not None:
        return row

    supabase = await get_supabase()
    default_row = {
        "user_id": str(user_id),
        "month": month,
        "scans_count": 0,
        "questions_per_scan": {},
    }
    try:
        upserted = (
            await supabase.table("usage_limits")
            .upsert(default_row, on_conflict="user_id,month", ignore_duplicates=True)
            .execute()
        )
    except Exception:
        logger.exception("upsert usage_limits failed user_id=%s month=%s", user_id, month)
        upserted = None

    if upserted and upserted.data:
        return upserted.data[0]

    # A concurrent request inserted the row first — read it back.
    row = await _read_row(user_id, month)
    return row if row is not None else default_row


def _looks_like_missing_rpc(exc: Exception, function_name: str) -> bool:
    """Detect Supabase/PostgREST schema-cache misses for newly added RPCs."""
    text = str(exc)
    return (
        "PGRST202" in text
        and function_name in text
        and ("Could not find the function" in text or "schema cache" in text)
    )


def _rpc_int_result(data: object, function_name: str) -> int:
    """Normalize Supabase RPC scalar return shapes.

    PostgREST returns scalar SQL function results as a bare JSON value, while
    tests and some client paths may expose a single-item list or object.
    """
    if data is None:
        return 0
    if isinstance(data, bool):
        return int(data)
    if isinstance(data, int | float | str):
        return int(data)
    if isinstance(data, list):
        if not data:
            return 0
        return _rpc_int_result(data[0], function_name)
    if isinstance(data, dict):
        for key in (function_name, "value", "count"):
            if key in data:
                return _rpc_int_result(data[key], function_name)
        if len(data) == 1:
            return _rpc_int_result(next(iter(data.values())), function_name)
    raise TypeError(f"Unexpected RPC result for {function_name}: {data!r}")


async def _increment_scan_count_fallback(user_id: UUID, month: str) -> int:
    """Non-atomic compatibility path used until the RPC migration is applied."""
    usage = await get_or_init_usage(user_id, month)
    new_count = int(usage.get("scans_count") or 0) + 1
    supabase = await get_supabase()
    result = (
        await supabase.table("usage_limits")
        .update({"scans_count": new_count})
        .eq("user_id", str(user_id))
        .eq("month", month)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Failed to increment scan count")
    return new_count


async def _increment_chat_count_fallback(user_id: UUID, scan_id: UUID, month: str) -> int:
    """Non-atomic compatibility path used until the RPC migration is applied."""
    usage = await get_or_init_usage(user_id, month)
    questions = dict(usage.get("questions_per_scan") or {})
    scan_key = str(scan_id)
    new_count = int(questions.get(scan_key, 0)) + 1
    questions[scan_key] = new_count

    supabase = await get_supabase()
    result = (
        await supabase.table("usage_limits")
        .update({"questions_per_scan": questions})
        .eq("user_id", str(user_id))
        .eq("month", month)
        .execute()
    )
    if not result.data:
        raise RuntimeError("Failed to increment chat count")
    return new_count


async def check_scan_quota(
    user_id: UUID,
    plan: UserPlan,
    page_count: int,
    month: str | None = None,
) -> None:
    """Raise QuotaExceededError if scan would exceed period cap or per-scan page cap.

    Advisory pre-check only — it races with concurrent requests. The
    authoritative gate is `try_increment_scan_count` at persist time.
    """
    caps = caps_for_plan(plan)
    if page_count > caps["max_pages_per_scan"]:
        raise QuotaExceededError(
            f"plan='{plan}' allows max {caps['max_pages_per_scan']} pages per scan (got {page_count})"
        )
    usage = await get_or_init_usage(user_id, month)
    scans_used = int(usage.get("scans_count") or 0)
    if scans_used >= caps["monthly_scans"]:
        raise QuotaExceededError(_scan_quota_message(plan, caps["monthly_scans"], scans_used))


def _scan_quota_message(plan: UserPlan, cap: int, scans_used: int | None = None) -> str:
    period_label = "month" if plan != "pro" else "subscription period"
    suffix = f", already used {scans_used}" if scans_used is not None else ""
    return f"plan='{plan}' allows {cap} scans/{period_label}{suffix}"


async def increment_scan_count(user_id: UUID, month: str | None = None) -> int:
    """Atomically bump scans_count by 1 and return the new value."""
    month = month or current_month()
    supabase = await get_supabase()
    try:
        result = await supabase.rpc(
            "increment_scan_count",
            {
                "p_user_id": str(user_id),
                "p_month": month,
            },
        ).execute()
    except Exception as exc:
        if not _looks_like_missing_rpc(exc, "increment_scan_count"):
            raise
        logger.warning(
            "Supabase RPC increment_scan_count is missing; using compatibility fallback. "
            "Apply supabase/migrations/0003_atomic_usage_counters.sql."
        )
        new_count = await _increment_scan_count_fallback(user_id, month)
        logger.info("Scan count user=%s month=%s -> %d", user_id, month, new_count)
        return new_count

    new_count = _rpc_int_result(result.data, "increment_scan_count")
    if new_count <= 0:
        raise RuntimeError("Failed to increment scan count")
    logger.info("Scan count user=%s month=%s -> %d", user_id, month, new_count)
    return new_count


async def try_increment_scan_count(user_id: UUID, plan: UserPlan, month: str | None = None) -> int:
    """Atomically increment scans_count only while below the plan cap.

    This is the authoritative quota gate: the conditional UPDATE inside the
    RPC re-evaluates the cap under the row lock, so N parallel requests at
    cap-1 can never all pass (unlike the advisory check_scan_quota).
    Returns the new count; raises QuotaExceededError when the cap is reached.
    """
    month = month or current_month()
    cap = caps_for_plan(plan)["monthly_scans"]
    supabase = await get_supabase()
    try:
        result = await supabase.rpc(
            "try_increment_scan_count",
            {
                "p_user_id": str(user_id),
                "p_month": month,
                "p_cap": cap,
            },
        ).execute()
    except Exception as exc:
        if not _looks_like_missing_rpc(exc, "try_increment_scan_count"):
            raise
        logger.warning(
            "Supabase RPC try_increment_scan_count is missing; using non-atomic fallback. "
            "Apply supabase/migrations/0017_conditional_quota_increments.sql."
        )
        usage = await get_or_init_usage(user_id, month)
        scans_used = int(usage.get("scans_count") or 0)
        if scans_used >= cap:
            raise QuotaExceededError(_scan_quota_message(plan, cap, scans_used)) from None
        return await increment_scan_count(user_id, month)

    new_count = _rpc_int_result(result.data, "try_increment_scan_count")
    if new_count < 0:
        raise QuotaExceededError(_scan_quota_message(plan, cap))
    if new_count == 0:
        raise RuntimeError("Failed to increment scan count")
    logger.info("Scan count user=%s month=%s -> %d", user_id, month, new_count)
    return new_count


async def check_chat_quota(
    user_id: UUID,
    plan: UserPlan,
    scan_id: UUID,
    month: str | None = None,
) -> int:
    """Raise QuotaExceededError if asking another question would exceed per-scan
    chat cap. Returns the count of questions already asked for this scan.

    Advisory pre-check only — the authoritative gate is
    `try_increment_chat_count` at charge time.
    """
    caps = caps_for_plan(plan)
    if caps["max_questions_per_scan"] <= 0:
        raise QuotaExceededError(f"plan='{plan}' does not include chat questions")

    usage = await get_or_init_usage(user_id, month)
    asked = int((usage.get("questions_per_scan") or {}).get(str(scan_id), 0))
    if asked >= caps["max_questions_per_scan"]:
        raise QuotaExceededError(_chat_quota_message(plan, caps["max_questions_per_scan"], asked))
    return asked


def _chat_quota_message(plan: UserPlan, cap: int, asked: int | None = None) -> str:
    suffix = f", already used {asked}" if asked is not None else ""
    return f"plan='{plan}' allows {cap} questions per scan{suffix}"


async def increment_chat_count(user_id: UUID, scan_id: UUID, month: str | None = None) -> int:
    """Atomically bump questions_per_scan[scan_id] by 1 and return the new value."""
    month = month or current_month()
    supabase = await get_supabase()
    try:
        result = await supabase.rpc(
            "increment_chat_count",
            {
                "p_user_id": str(user_id),
                "p_month": month,
                "p_scan_id": str(scan_id),
            },
        ).execute()
    except Exception as exc:
        if not _looks_like_missing_rpc(exc, "increment_chat_count"):
            raise
        logger.warning(
            "Supabase RPC increment_chat_count is missing; using compatibility fallback. "
            "Apply supabase/migrations/0003_atomic_usage_counters.sql."
        )
        new_count = await _increment_chat_count_fallback(user_id, scan_id, month)
        logger.info("Chat count user=%s scan=%s month=%s -> %d", user_id, scan_id, month, new_count)
        return new_count

    new_count = _rpc_int_result(result.data, "increment_chat_count")
    if new_count <= 0:
        raise RuntimeError("Failed to increment chat count")
    logger.info("Chat count user=%s scan=%s month=%s -> %d", user_id, scan_id, month, new_count)
    return new_count


async def try_increment_chat_count(
    user_id: UUID,
    plan: UserPlan,
    scan_id: UUID,
    month: str | None = None,
) -> int:
    """Atomically increment questions_per_scan[scan_id] only while below cap.

    Authoritative per-scan chat quota gate (see try_increment_scan_count).
    Returns the new count; raises QuotaExceededError at the cap.
    """
    month = month or current_month()
    cap = caps_for_plan(plan)["max_questions_per_scan"]
    if cap <= 0:
        raise QuotaExceededError(f"plan='{plan}' does not include chat questions")

    supabase = await get_supabase()
    try:
        result = await supabase.rpc(
            "try_increment_chat_count",
            {
                "p_user_id": str(user_id),
                "p_month": month,
                "p_scan_id": str(scan_id),
                "p_cap": cap,
            },
        ).execute()
    except Exception as exc:
        if not _looks_like_missing_rpc(exc, "try_increment_chat_count"):
            raise
        logger.warning(
            "Supabase RPC try_increment_chat_count is missing; using non-atomic fallback. "
            "Apply supabase/migrations/0017_conditional_quota_increments.sql."
        )
        usage = await get_or_init_usage(user_id, month)
        asked = int((usage.get("questions_per_scan") or {}).get(str(scan_id), 0))
        if asked >= cap:
            raise QuotaExceededError(_chat_quota_message(plan, cap, asked)) from None
        return await increment_chat_count(user_id, scan_id, month)

    new_count = _rpc_int_result(result.data, "try_increment_chat_count")
    if new_count < 0:
        raise QuotaExceededError(_chat_quota_message(plan, cap))
    if new_count == 0:
        raise RuntimeError("Failed to increment chat count")
    logger.info("Chat count user=%s scan=%s month=%s -> %d", user_id, scan_id, month, new_count)
    return new_count
