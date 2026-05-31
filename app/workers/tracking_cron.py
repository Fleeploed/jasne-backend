from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.models.schemas import OampTrackingStatus
from app.services.oamp_client import check_status
from app.services.push import send_status_change_push
from app.services.rag import close_supabase, get_supabase

logger = logging.getLogger(__name__)

CRON_BATCH_LIMIT = 200
OAMP_REQUEST_DELAY_SECONDS = 0.5
OAMP_MAX_CONCURRENT_CHECKS = 8
NOT_FOUND_STOP_THRESHOLD = 3
ERROR_STOP_THRESHOLD = 5


@dataclass
class TrackingCronStats:
    due: int = 0
    checked: int = 0
    changed: int = 0
    pushes_attempted: int = 0
    deactivated: int = 0
    errors: int = 0


async def run_tracking_cron(
    *,
    limit: int = CRON_BATCH_LIMIT,
    delay_seconds: float = OAMP_REQUEST_DELAY_SECONDS,
    max_concurrent_checks: int = OAMP_MAX_CONCURRENT_CHECKS,
) -> dict[str, int]:
    stats = TrackingCronStats()
    supabase = await get_supabase()
    now = datetime.now(UTC)

    result = (
        await supabase.table("tracked_applications")
        .select("*")
        .eq("is_active", True)
        .lt("next_check_at", now.isoformat())
        .order("next_check_at")
        .limit(limit)
        .execute()
    )
    rows = result.data or []
    stats.due = len(rows)

    semaphore = asyncio.Semaphore(max(1, max_concurrent_checks))
    tasks = [
        asyncio.create_task(_process_row(supabase, row, index, delay_seconds, semaphore, stats))
        for index, row in enumerate(rows)
    ]
    if tasks:
        await asyncio.gather(*tasks)

    logger.info("Tracking cron finished stats=%s", asdict(stats))
    return asdict(stats)


async def _process_row(
    supabase,
    row: dict[str, Any],
    index: int,
    delay_seconds: float,
    semaphore: asyncio.Semaphore,
    stats: TrackingCronStats,
) -> None:
    if delay_seconds > 0 and index > 0:
        await asyncio.sleep(index * delay_seconds)

    async with semaphore:
        try:
            new_status = await check_status(str(row.get("cislo_jednaci") or ""))
            await _apply_status_result(supabase, row, new_status, stats)
            stats.checked += 1
        except Exception:
            stats.errors += 1
            logger.exception("Tracking check crashed id=%s", row.get("id"))


async def _apply_status_result(
    supabase,
    row: dict[str, Any],
    new_status: OampTrackingStatus,
    stats: TrackingCronStats,
) -> None:
    now = datetime.now(UTC)
    old_status = _tracking_status(row.get("current_status"))
    user_id = str(row["user_id"])
    cislo_jednaci = str(row["cislo_jednaci"])
    language = await _language_for_user(supabase, user_id)

    update: dict[str, Any] = {
        "current_status": new_status,
        "last_checked_at": now.isoformat(),
        "consecutive_not_found_count": 0,
        "consecutive_error_count": 0,
    }

    if new_status != old_status:
        stats.changed += 1

    if new_status in {"schvaleno", "zamitnuto"}:
        sent = 0
        notification_count = int(row.get("notification_count") or 0)
        if new_status != old_status:
            sent = await send_status_change_push(user_id, cislo_jednaci, new_status, language)
            notification_count += 1
        update.update(
            {
                "is_active": False,
                "next_check_at": now.isoformat(),
                "notification_count": notification_count,
            }
        )
        stats.pushes_attempted += 1 if sent else 0
        stats.deactivated += 1
    elif new_status == "not_found":
        not_found_count = _next_retry_count(row, "consecutive_not_found_count", old_status == "not_found")
        update["consecutive_not_found_count"] = not_found_count
        update["next_check_at"] = (now + timedelta(days=1)).isoformat()
        if not_found_count >= NOT_FOUND_STOP_THRESHOLD:
            sent = await send_status_change_push(user_id, cislo_jednaci, "not_found_stopped", language)
            update["is_active"] = False
            update["notification_count"] = int(row.get("notification_count") or 0) + 1
            stats.pushes_attempted += 1 if sent else 0
            stats.deactivated += 1
    elif new_status == "error":
        error_count = _next_retry_count(row, "consecutive_error_count", old_status == "error")
        update["consecutive_error_count"] = error_count
        update["next_check_at"] = (now + timedelta(hours=1)).isoformat()
        if error_count >= ERROR_STOP_THRESHOLD:
            sent = await send_status_change_push(user_id, cislo_jednaci, "error_stopped", language)
            update["is_active"] = False
            update["notification_count"] = int(row.get("notification_count") or 0) + 1
            stats.pushes_attempted += 1 if sent else 0
            stats.deactivated += 1
    elif new_status == "probiha":
        update["next_check_at"] = _next_probiha_check(now, _parse_datetime(row.get("created_at"))).isoformat()
    else:
        update["next_check_at"] = (now + timedelta(hours=1)).isoformat()

    await supabase.table("tracked_applications").update(update).eq("id", str(row["id"])).execute()
    logger.info(
        "tracking_check id=%s case=%s old=%s new=%s active=%s",
        row.get("id"),
        mask_cislo_jednaci(cislo_jednaci),
        old_status,
        new_status,
        update.get("is_active", row.get("is_active")),
    )


async def _language_for_user(supabase, user_id: str) -> str:
    result = await supabase.table("profiles").select("language").eq("id", user_id).limit(1).execute()
    rows = result.data or []
    language = rows[0].get("language") if rows else None
    return language if language in {"cs", "en", "uk", "ru"} else "cs"


def _next_retry_count(row: dict[str, Any], column: str, was_same_status: bool) -> int:
    if not was_same_status:
        return 1
    return int(row.get(column) or 0) + 1


def _next_probiha_check(now: datetime, created_at: datetime) -> datetime:
    age = now - created_at
    if age < timedelta(days=30):
        return now + timedelta(days=7)
    if age <= timedelta(days=90):
        return now + timedelta(days=3)
    return now + timedelta(days=1)


def _tracking_status(value: Any) -> OampTrackingStatus:
    if value in {"probiha", "schvaleno", "zamitnuto", "not_found", "error"}:
        return value
    return "error"


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        return datetime.now(UTC)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def mask_cislo_jednaci(value: str) -> str:
    cleaned = value.strip()
    if "/" in cleaned and cleaned.upper().startswith("OAM-"):
        suffix = cleaned.split("/", 1)[1]
        return f"OAM-***/{suffix}"
    if len(cleaned) <= 8:
        return "***"
    return f"{cleaned[:3]}***{cleaned[-4:]}"


async def main() -> None:
    try:
        await run_tracking_cron()
    finally:
        await close_supabase()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    asyncio.run(main())
