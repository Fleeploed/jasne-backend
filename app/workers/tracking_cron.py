from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from app.services.push import send_tracking_push
from app.services.rag import close_supabase, get_supabase
from app.services.trackers.registry import get_tracker

logger = logging.getLogger(__name__)

CRON_BATCH_LIMIT = 200
TRACKING_REQUEST_DELAY_SECONDS = 0.5


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
    delay_seconds: float = TRACKING_REQUEST_DELAY_SECONDS,
) -> dict[str, int]:
    stats = TrackingCronStats()
    supabase = await get_supabase()
    now = datetime.now(UTC)

    result = (
        await supabase.table("trackings")
        .select("*")
        .eq("is_active", True)
        .lt("next_check_at", now.isoformat())
        .order("next_check_at")
        .limit(limit)
        .execute()
    )
    rows = result.data or []
    stats.due = len(rows)

    for index, row in enumerate(rows):
        if delay_seconds > 0 and index > 0:
            await asyncio.sleep(delay_seconds)
        await _process_row(supabase, row, stats)

    logger.info("Tracking cron finished stats=%s", asdict(stats))
    return asdict(stats)


async def _process_row(supabase, row: dict[str, Any], stats: TrackingCronStats) -> None:
    tracking_type = str(row.get("tracking_type") or "")
    identifier = str(row.get("identifier") or "")

    try:
        tracker = get_tracker(tracking_type)
        metadata = _metadata(row)
        result = await tracker.check(identifier, metadata)
        await _apply_check_result(supabase, row, result.status, result.raw_data, stats)
        stats.checked += 1
    except Exception:
        stats.errors += 1
        logger.exception(
            "tracker_check_failed type=%s identifier=%s",
            tracking_type or "unknown",
            mask_identifier(identifier),
        )


async def _apply_check_result(
    supabase,
    row: dict[str, Any],
    new_status: str,
    raw_data: dict[str, Any],
    stats: TrackingCronStats,
) -> None:
    now = datetime.now(UTC)
    tracking_type = str(row["tracking_type"])
    identifier = str(row["identifier"])
    old_status = str(row.get("current_status") or "")
    tracker = get_tracker(tracking_type)
    metadata = _metadata(row)
    metadata.update(raw_data)

    projected_tracking = {
        **row,
        "current_status": new_status,
        "metadata": metadata,
    }
    next_check_at = now + tracker.get_next_check_interval(projected_tracking)
    is_terminal = tracker.is_terminal_status(new_status)
    notification_count = int(row.get("notification_count") or 0)
    update: dict[str, Any] = {
        "current_status": new_status,
        "metadata": metadata,
        "last_checked_at": now.isoformat(),
        "next_check_at": next_check_at.isoformat(),
        "is_active": False if is_terminal else row.get("is_active", True),
        "notification_count": notification_count,
    }

    if new_status != old_status:
        stats.changed += 1
        language = await _language_for_user(supabase, str(row["user_id"]))
        title, body = tracker.get_push_message(language, new_status, identifier, metadata)
        sent = await send_tracking_push(
            str(row["user_id"]),
            tracking_type,
            identifier,
            new_status,
            title,
            body,
        )
        update["notification_count"] = notification_count + (1 if sent else 0)
        stats.pushes_attempted += 1 if sent else 0

    if is_terminal:
        stats.deactivated += 1

    await supabase.table("trackings").update(update).eq("id", str(row["id"])).execute()
    logger.info(
        "tracker_check type=%s identifier=%s result=%s next_check=%s",
        tracking_type,
        mask_identifier(identifier),
        new_status,
        next_check_at.date().isoformat(),
    )


async def _language_for_user(supabase, user_id: str) -> str:
    result = await supabase.table("profiles").select("language").eq("id", user_id).limit(1).execute()
    rows = result.data or []
    language = rows[0].get("language") if rows else None
    return language if language in {"cs", "en", "uk", "ru"} else "cs"


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("metadata")
    return dict(value) if isinstance(value, dict) else {}


def mask_identifier(value: str) -> str:
    cleaned = value.strip()
    if "/" in cleaned and cleaned.upper().startswith("OAM-"):
        suffix = cleaned.split("/", 1)[1]
        return f"OAM-***/{suffix}"
    if len(cleaned) <= 8:
        return "***"
    return f"{cleaned[:3]}***{cleaned[-4:]}"


mask_cislo_jednaci = mask_identifier


async def main() -> None:
    try:
        await run_tracking_cron()
    finally:
        await close_supabase()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    asyncio.run(main())
