"""Usage limits enforcement — Free vs Pro plans.

Skill caps:
  Free → 5 scans/month, 3 pages/scan, 0 chat questions
  Pro  → 100 scans/month, 10 pages/scan, 10 chat questions per scan

Counters live in `usage_limits` (composite PK on user_id+month). We prefer
Postgres RPC functions so concurrent scan/chat requests cannot lose updates,
with a compatibility fallback for environments where the SQL migration has not
been applied yet.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TypedDict
from uuid import UUID

from app.models.schemas import UserPlan
from app.services.rag import get_supabase

logger = logging.getLogger(__name__)


class PlanCaps(TypedDict):
    monthly_scans: int
    max_pages_per_scan: int
    max_questions_per_scan: int


PLAN_LIMITS: dict[UserPlan, PlanCaps] = {
    "free": {
        "monthly_scans": 5,
        "max_pages_per_scan": 3,
        "max_questions_per_scan": 0,
    },
    "pro": {
        "monthly_scans": 100,
        "max_pages_per_scan": 10,
        "max_questions_per_scan": 10,
    },
}


def caps_for_plan(plan: UserPlan) -> PlanCaps:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])


def current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


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
    """Read the current month's row, creating it if missing.

    Uses upsert with ignore_duplicates to avoid PK-violation races when two
    requests arrive before the month's row exists.
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


async def check_scan_quota(user_id: UUID, plan: UserPlan, page_count: int) -> None:
    """Raise ValueError if scan would exceed monthly cap or per-scan page cap."""
    caps = caps_for_plan(plan)
    if page_count > caps["max_pages_per_scan"]:
        raise ValueError(
            f"plan='{plan}' allows max {caps['max_pages_per_scan']} pages per scan (got {page_count})"
        )
    usage = await get_or_init_usage(user_id)
    if usage["scans_count"] >= caps["monthly_scans"]:
        raise ValueError(
            f"plan='{plan}' allows {caps['monthly_scans']} scans/month, already used {usage['scans_count']}"
        )


async def increment_scan_count(user_id: UUID) -> int:
    """Atomically bump scans_count by 1 and return the new value."""
    month = current_month()
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


async def check_chat_quota(user_id: UUID, plan: UserPlan, scan_id: UUID) -> int:
    """Raise ValueError if asking another question would exceed per-scan chat cap.
    Returns the count of questions already asked for this scan."""
    caps = caps_for_plan(plan)
    if caps["max_questions_per_scan"] <= 0:
        raise ValueError(f"plan='{plan}' does not include chat questions")

    usage = await get_or_init_usage(user_id)
    asked = int((usage.get("questions_per_scan") or {}).get(str(scan_id), 0))
    if asked >= caps["max_questions_per_scan"]:
        raise ValueError(
            f"plan='{plan}' allows {caps['max_questions_per_scan']} questions per scan, already used {asked}"
        )
    return asked


async def increment_chat_count(user_id: UUID, scan_id: UUID) -> int:
    """Atomically bump questions_per_scan[scan_id] by 1 and return the new value."""
    month = current_month()
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
