from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import get_settings
from app.models.schemas import UserLanguage
from app.services.rag import get_supabase

logger = logging.getLogger(__name__)

FREE_REMINDER_DAYS = {1}
PRO_REMINDER_DAYS = {30, 14, 7, 1}
EXPO_PUSH_CHUNK_SIZE = 100

# The furthest-out reminder any plan can fire (Pro's 30-day notice). A deadline
# due beyond this window can't produce a reminder today, so the cron query
# bounds itself to [today, today + this] instead of scanning every active row.
MAX_REMINDER_HORIZON_DAYS = max(PRO_REMINDER_DAYS)

# The audience is entirely in Czechia, so "today" must be the Prague calendar
# date. A cron firing at 23:30 UTC is already tomorrow in Prague — computing
# reminder_day from the UTC date would shift or duplicate 1-day reminders.
USER_TIMEZONE = ZoneInfo("Europe/Prague")


@dataclass
class NotificationRunStats:
    scanned_deadlines: int = 0
    candidate_notifications: int = 0
    attempted: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0


# Expo recommends waiting ~15 minutes before fetching receipts; check at 20 so
# a single daily receipts run is reliably past that.
RECEIPT_MIN_AGE_MINUTES = 20
RECEIPT_BATCH_SIZE = 300


@dataclass
class ReceiptRunStats:
    checked: int = 0
    delivered: int = 0
    failed: int = 0
    tokens_disabled: int = 0
    pending: int = 0


async def send_due_deadline_notifications(today: date | None = None) -> dict[str, int]:
    """Send deadline reminders whose reminder day falls on `today`.

    The function is intentionally idempotent: `notification_deliveries` has a
    unique key per deadline/reminder/token, so cron retries do not double-send.
    """
    run_date = today or datetime.now(USER_TIMEZONE).date()
    stats = NotificationRunStats()
    supabase = await get_supabase()
    profile_cache: dict[str, dict[str, Any]] = {}
    token_cache: dict[str, list[dict[str, Any]]] = {}

    # Only pull rows that could fire a reminder today: due on/after today and
    # no further out than the longest reminder window. due_date is stored as an
    # ISO date string, which sorts lexicographically, so range filters are safe.
    horizon = run_date + timedelta(days=MAX_REMINDER_HORIZON_DAYS)
    result = (
        await supabase.table("deadlines")
        .select("*")
        .eq("status", "active")
        .gte("due_date", run_date.isoformat())
        .lte("due_date", horizon.isoformat())
        .execute()
    )
    pending: list[dict[str, Any]] = []

    for deadline in result.data or []:
        stats.scanned_deadlines += 1
        due_date = _parse_date(deadline.get("due_date"))
        if due_date is None or due_date < run_date:
            continue

        reminder_day = (due_date - run_date).days
        reminder_days = _reminder_days(deadline.get("reminder_days"))
        if reminder_day not in reminder_days:
            continue

        user_id = str(deadline.get("user_id") or "")
        if not user_id:
            stats.skipped += 1
            continue

        profile = await _profile_for_user(user_id, profile_cache)
        if not _profile_allows_reminder(profile, reminder_day):
            stats.skipped += 1
            continue

        stats.candidate_notifications += 1
        tokens = await _active_tokens_for_user(user_id, token_cache)
        if not tokens:
            stats.skipped += 1
            continue

        for token in tokens:
            delivery = await _create_delivery(deadline, token, reminder_day, run_date)
            if delivery is None:
                stats.skipped += 1
                continue

            pending.append(
                {
                    "delivery": delivery,
                    "token": token,
                    "message": _build_expo_message(deadline, token, profile, reminder_day, due_date),
                }
            )

    if pending:
        await _send_pending_notifications(pending, stats)

    return asdict(stats)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _reminder_days(value: Any) -> set[int]:
    if not isinstance(value, list):
        return set()
    days: set[int] = set()
    for item in value:
        try:
            day = int(item)
        except (TypeError, ValueError):
            continue
        if day >= 0:
            days.add(day)
    return days


def _allowed_reminder_days(plan: str) -> set[int]:
    return PRO_REMINDER_DAYS if plan == "pro" else FREE_REMINDER_DAYS


def _profile_allows_reminder(profile: dict[str, Any], reminder_day: int) -> bool:
    # Accounts inside the deletion grace period must not receive reminders:
    # push_tokens rows stay enabled for a clean restore, so filter here.
    if profile.get("deletion_status") == "pending":
        return False
    return reminder_day in _allowed_reminder_days(profile.get("plan", "free"))


async def _profile_for_user(user_id: str, cache: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if user_id in cache:
        return cache[user_id]

    supabase = await get_supabase()
    result = (
        await supabase.table("profiles")
        .select("id,plan,language,deletion_status")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    profile = result.data[0] if result.data else {"id": user_id, "plan": "free", "language": "cs"}
    cache[user_id] = profile
    return profile


async def _active_tokens_for_user(
    user_id: str,
    cache: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    # Cached per cron run — a user with many deadlines due otherwise costs one
    # push_tokens query per deadline (N+1).
    if cache is not None and user_id in cache:
        return cache[user_id]

    supabase = await get_supabase()
    result = (
        await supabase.table("push_tokens").select("*").eq("user_id", user_id).eq("enabled", True).execute()
    )
    tokens = result.data or []
    if cache is not None:
        cache[user_id] = tokens
    return tokens


async def _create_delivery(
    deadline: dict[str, Any],
    token: dict[str, Any],
    reminder_day: int,
    scheduled_for: date,
) -> dict[str, Any] | None:
    supabase = await get_supabase()
    payload = {
        "user_id": str(deadline["user_id"]),
        "deadline_id": str(deadline["id"]),
        "push_token_id": str(token["id"]),
        "reminder_day": reminder_day,
        "scheduled_for": scheduled_for.isoformat(),
        "status": "pending",
    }
    result = (
        await supabase.table("notification_deliveries")
        .upsert(
            payload,
            on_conflict="deadline_id,reminder_day,push_token_id",
            ignore_duplicates=True,
        )
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


async def _send_pending_notifications(
    pending: list[dict[str, Any]],
    stats: NotificationRunStats,
) -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=10.0) as client:
        for start in range(0, len(pending), EXPO_PUSH_CHUNK_SIZE):
            chunk = pending[start : start + EXPO_PUSH_CHUNK_SIZE]
            messages = [item["message"] for item in chunk]
            try:
                tickets = await _post_expo_messages(client, settings.expo_push_api_url, messages)
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                logger.exception("Expo push chunk failed size=%d", len(chunk))
                for item in chunk:
                    await _mark_delivery_error(item["delivery"], str(exc))
                    stats.failed += 1
                stats.attempted += len(chunk)
                continue

            stats.attempted += len(chunk)
            for item, ticket in zip(chunk, tickets, strict=False):
                await _handle_ticket(item, ticket, stats)

            if len(tickets) < len(chunk):
                for item in chunk[len(tickets) :]:
                    await _mark_delivery_error(item["delivery"], "Expo push response omitted ticket")
                    stats.failed += 1


async def _post_expo_messages(
    client: httpx.AsyncClient,
    url: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    response = await client.post(url, json=messages, headers={"Content-Type": "application/json"})
    response.raise_for_status()
    body = response.json()
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        return [
            item if isinstance(item, dict) else {"status": "error", "message": str(item)}
            for item in body["data"]
        ]
    if isinstance(body, list):
        return [
            item if isinstance(item, dict) else {"status": "error", "message": str(item)} for item in body
        ]
    raise RuntimeError("Unexpected Expo push response")


async def _handle_ticket(item: dict[str, Any], ticket: dict[str, Any], stats: NotificationRunStats) -> None:
    delivery = item["delivery"]
    token = item["token"]
    ticket_id = ticket.get("id") if isinstance(ticket.get("id"), str) else None
    if ticket.get("status") == "ok":
        await _mark_delivery_sent(delivery, ticket_id)
        stats.sent += 1
        return

    error = _ticket_error(ticket)
    await _mark_delivery_error(delivery, error, ticket_id=ticket_id)
    if error == "DeviceNotRegistered":
        await _disable_token(token)
    stats.failed += 1


def _ticket_error(ticket: dict[str, Any]) -> str:
    details = ticket.get("details")
    if isinstance(details, dict) and isinstance(details.get("error"), str):
        return details["error"]
    if isinstance(ticket.get("message"), str):
        return ticket["message"]
    return "Expo push error"


async def _mark_delivery_sent(delivery: dict[str, Any], ticket_id: str | None) -> None:
    supabase = await get_supabase()
    await (
        supabase.table("notification_deliveries")
        .update(
            {
                "status": "sent",
                "sent_at": datetime.now(UTC).isoformat(),
                "expo_ticket_id": ticket_id,
                "error": None,
            }
        )
        .eq("id", str(delivery["id"]))
        .execute()
    )


async def _mark_delivery_error(
    delivery: dict[str, Any],
    error: str,
    *,
    ticket_id: str | None = None,
) -> None:
    supabase = await get_supabase()
    await (
        supabase.table("notification_deliveries")
        .update(
            {
                "status": "error",
                "sent_at": datetime.now(UTC).isoformat(),
                "expo_ticket_id": ticket_id,
                "error": error[:500],
            }
        )
        .eq("id", str(delivery["id"]))
        .execute()
    )


async def _disable_token(token: dict[str, Any]) -> None:
    supabase = await get_supabase()
    await supabase.table("push_tokens").update({"enabled": False}).eq("id", str(token["id"])).execute()


async def check_expo_push_receipts(now: datetime | None = None) -> dict[str, int]:
    """Fetch Expo receipts for sent notifications and disable dead tokens.

    An "ok" *ticket* does not mean delivery — DeviceNotRegistered often shows
    up only in the *receipt*. Without this job dead tokens accumulate and every
    cron run wastes sends on them. Receipts Expo hasn't produced yet stay in
    status='sent' and are retried on the next run.
    """
    run_now = now or datetime.now(UTC)
    cutoff = (run_now - timedelta(minutes=RECEIPT_MIN_AGE_MINUTES)).isoformat()
    stats = ReceiptRunStats()
    supabase = await get_supabase()

    result = (
        await supabase.table("notification_deliveries")
        .select("*")
        .eq("status", "sent")
        .lte("sent_at", cutoff)
        .limit(1000)
        .execute()
    )
    deliveries = [row for row in (result.data or []) if row.get("expo_ticket_id")]
    if not deliveries:
        return asdict(stats)

    by_ticket = {str(row["expo_ticket_id"]): row for row in deliveries}
    ticket_ids = list(by_ticket.keys())
    settings = get_settings()

    async with httpx.AsyncClient(timeout=10.0) as client:
        for start in range(0, len(ticket_ids), RECEIPT_BATCH_SIZE):
            chunk = ticket_ids[start : start + RECEIPT_BATCH_SIZE]
            try:
                response = await client.post(
                    settings.expo_push_receipts_url,
                    json={"ids": chunk},
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                receipts = response.json().get("data") or {}
            except (httpx.HTTPError, ValueError):
                logger.exception("Expo receipts fetch failed size=%d", len(chunk))
                continue

            for ticket_id in chunk:
                delivery = by_ticket[ticket_id]
                receipt = receipts.get(ticket_id)
                if not isinstance(receipt, dict):
                    # Expo hasn't produced the receipt yet — retry next run.
                    stats.pending += 1
                    continue

                stats.checked += 1
                if receipt.get("status") == "ok":
                    await _mark_receipt_ok(delivery, ticket_id)
                    stats.delivered += 1
                    continue

                error = _ticket_error(receipt)
                await _mark_delivery_error(delivery, error, ticket_id=ticket_id)
                stats.failed += 1
                if error == "DeviceNotRegistered" and delivery.get("push_token_id"):
                    await _disable_token({"id": delivery["push_token_id"]})
                    stats.tokens_disabled += 1

    logger.info(
        "Expo receipts run checked=%d delivered=%d failed=%d tokens_disabled=%d pending=%d",
        stats.checked,
        stats.delivered,
        stats.failed,
        stats.tokens_disabled,
        stats.pending,
    )
    return asdict(stats)


async def _mark_receipt_ok(delivery: dict[str, Any], ticket_id: str) -> None:
    supabase = await get_supabase()
    await (
        supabase.table("notification_deliveries")
        .update(
            {
                "status": "receipt_ok",
                "expo_receipt_id": ticket_id,
                "error": None,
            }
        )
        .eq("id", str(delivery["id"]))
        .execute()
    )


def _build_expo_message(
    deadline: dict[str, Any],
    token: dict[str, Any],
    profile: dict[str, Any],
    reminder_day: int,
    due_date: date,
) -> dict[str, Any]:
    language = _language(profile.get("language"))
    title, body = _copy_for(language, str(deadline.get("title") or ""), reminder_day, due_date)
    return {
        "to": token["expo_push_token"],
        "title": title,
        "body": body,
        "sound": "default",
        "channelId": "deadline-reminders",
        "data": {
            "type": "deadline_reminder",
            "deadline_id": str(deadline["id"]),
            "scan_id": str(deadline["scan_id"]) if deadline.get("scan_id") else None,
            "reminder_day": reminder_day,
        },
    }


def _language(value: Any) -> UserLanguage:
    return value if value in {"cs", "en", "uk", "ru"} else "cs"


def _copy_for(
    language: UserLanguage, deadline_title: str, reminder_day: int, due_date: date
) -> tuple[str, str]:
    date_label = f"{due_date.day}.{due_date.month}.{due_date.year}"
    clean_title = (
        deadline_title.strip()
        or {
            "cs": "termín",
            "en": "deadline",
            "uk": "строк",
            "ru": "срок",
        }[language]
    )
    if language == "ru":
        return f"Напоминание: {clean_title}"[:120], f"Срок {date_label}. Осталось {_days_ru(reminder_day)}."
    if language == "uk":
        return f"Нагадування: {clean_title}"[
            :120
        ], f"Строк {date_label}. Залишилось {_days_uk(reminder_day)}."
    if language == "en":
        return f"Reminder: {clean_title}"[:120], f"Due {date_label}. {reminder_day} day(s) left."
    return f"Připomínka: {clean_title}"[:120], f"Termín {date_label}. Zbývá {reminder_day} d."


def _days_ru(value: int) -> str:
    if value == 1:
        return "1 день"
    if 2 <= value <= 4:
        return f"{value} дня"
    return f"{value} дней"


def _days_uk(value: int) -> str:
    if value == 1:
        return "1 день"
    if 2 <= value <= 4:
        return f"{value} дні"
    return f"{value} днів"
