"""Usage limits enforcement — Free vs Pro plans.

Skill caps:
  Free → 5 scans/month, 3 pages/scan, 0 chat questions
  Pro  → 100 scans/month, 10 pages/scan, 10 chat questions per scan

Counters live in `usage_limits` (composite PK on user_id+month). We always
read-modify-write under the assumption that one user's traffic is sequential
(no realistic race for monthly counters in our scale). If we ever see contention
we'll move to atomic SQL `update ... returning`.
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
    """Read the current month's row, creating it if missing."""
    month = month or current_month()
    row = await _read_row(user_id, month)
    if row is not None:
        return row

    supabase = await get_supabase()
    inserted = (
        await supabase.table("usage_limits")
        .insert(
            {
                "user_id": str(user_id),
                "month": month,
                "scans_count": 0,
                "questions_per_scan": {},
            }
        )
        .execute()
    )
    return (
        inserted.data[0]
        if inserted.data
        else {
            "user_id": str(user_id),
            "month": month,
            "scans_count": 0,
            "questions_per_scan": {},
        }
    )


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
    """Bump scans_count by 1 and return the new value."""
    month = current_month()
    usage = await get_or_init_usage(user_id, month)
    new_count = int(usage["scans_count"]) + 1
    supabase = await get_supabase()
    await (
        supabase.table("usage_limits")
        .update({"scans_count": new_count})
        .eq("user_id", str(user_id))
        .eq("month", month)
        .execute()
    )
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
    """Bump questions_per_scan[scan_id] by 1 and return the new value."""
    month = current_month()
    usage = await get_or_init_usage(user_id, month)
    qmap: dict[str, int] = dict(usage.get("questions_per_scan") or {})
    new_count = int(qmap.get(str(scan_id), 0)) + 1
    qmap[str(scan_id)] = new_count
    supabase = await get_supabase()
    await (
        supabase.table("usage_limits")
        .update({"questions_per_scan": qmap})
        .eq("user_id", str(user_id))
        .eq("month", month)
        .execute()
    )
    logger.info("Chat count user=%s scan=%s month=%s -> %d", user_id, scan_id, month, new_count)
    return new_count
