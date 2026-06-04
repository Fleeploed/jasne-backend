"""Soft-delete account lifecycle helpers."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException, status

from app.config import get_settings
from app.services.rag import get_supabase
from app.services.supabase_admin import get_supabase_admin

DELETION_GRACE_DAYS = 7
DELETION_STATUS_ACTIVE = "active"
DELETION_STATUS_PENDING = "pending"
PURGE_BATCH_LIMIT = 50

logger = logging.getLogger(__name__)


def now_utc() -> datetime:
    return datetime.now(UTC)


def build_pending_deletion_update(reason: str | None, now: datetime | None = None) -> dict[str, Any]:
    requested_at = now or now_utc()
    scheduled_at = requested_at + timedelta(days=DELETION_GRACE_DAYS)
    return {
        "deletion_status": DELETION_STATUS_PENDING,
        "deletion_requested_at": requested_at.isoformat(),
        "deletion_scheduled_at": scheduled_at.isoformat(),
        "deletion_reason": reason,
        "deletion_restored_at": None,
        # Stop reminders/pushes while the account is pending deletion.
        "expo_push_token": None,
    }


def build_restore_update(now: datetime | None = None) -> dict[str, Any]:
    restored_at = now or now_utc()
    return {
        "deletion_status": DELETION_STATUS_ACTIVE,
        "deletion_requested_at": None,
        "deletion_scheduled_at": None,
        "deletion_reason": None,
        "deletion_restored_at": restored_at.isoformat(),
    }


def is_pending_deletion(profile: dict[str, Any] | None) -> bool:
    if not profile:
        return False
    return profile.get("deletion_status") == DELETION_STATUS_PENDING


async def fetch_deletion_profile(user_id: UUID) -> dict[str, Any] | None:
    supabase = await get_supabase()
    result = (
        await supabase.table("profiles")
        .select("id,deletion_status,deletion_requested_at,deletion_scheduled_at")
        .eq("id", str(user_id))
        .limit(1)
        .execute()
    )
    return (result.data or [None])[0]


async def ensure_account_active(user_id: UUID) -> None:
    profile = await fetch_deletion_profile(user_id)
    if is_pending_deletion(profile):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "account_pending_deletion",
                "message": "Account is pending deletion. Restore it before using the API.",
                "deletion_scheduled_at": profile.get("deletion_scheduled_at"),
            },
        )


async def purge_due_deleted_accounts(limit: int = PURGE_BATCH_LIMIT) -> dict[str, int]:
    """Permanently delete accounts whose 7-day grace period has elapsed."""
    supabase = await get_supabase()
    now = now_utc().isoformat()
    result = (
        await supabase.table("profiles")
        .select("id,deletion_scheduled_at")
        .eq("deletion_status", DELETION_STATUS_PENDING)
        .lte("deletion_scheduled_at", now)
        .limit(max(1, min(limit, PURGE_BATCH_LIMIT)))
        .execute()
    )
    rows = result.data or []
    stats = {
        "scanned_profiles": len(rows),
        "purged": 0,
        "failed": 0,
    }

    for row in rows:
        user_id = str(row.get("id") or "")
        if not user_id:
            stats["failed"] += 1
            continue

        try:
            await _purge_one_account(supabase, user_id)
            stats["purged"] += 1
        except Exception:
            stats["failed"] += 1
            logger.error("Account purge failed user_prefix=%s", user_id[:8], exc_info=True)

    return stats


async def _purge_one_account(supabase: Any, user_id: str) -> None:
    try:
        await _delete_revenuecat_subscriber(user_id)
    except Exception:
        logger.warning("RevenueCat subscriber purge failed user_prefix=%s", user_id[:8], exc_info=True)

    try:
        await _delete_storage_objects(supabase, user_id)
    except Exception:
        logger.warning("Storage purge failed user_prefix=%s", user_id[:8], exc_info=True)

    await _delete_user_rows(supabase, user_id)
    await _delete_auth_user(user_id)
    await supabase.table("profiles").delete().eq("id", user_id).execute()
    logger.info("account_purged user_prefix=%s", user_id[:8])


async def _delete_user_rows(supabase: Any, user_id: str) -> None:
    for table_name in ("push_tokens", "deadlines", "trackings", "usage_limits"):
        try:
            await supabase.table(table_name).delete().eq("user_id", user_id).execute()
        except Exception:
            logger.warning("Table purge failed table=%s user_prefix=%s", table_name, user_id[:8], exc_info=True)

    try:
        await supabase.rpc("delete_user_scans", {"p_user_id": user_id}).execute()
        return
    except Exception:
        logger.warning("delete_user_scans RPC failed during account purge", exc_info=True)

    await supabase.table("scans").delete().eq("user_id", user_id).execute()


async def _delete_storage_objects(supabase: Any, user_id: str) -> None:
    result = await supabase.table("scans").select("image_paths").eq("user_id", user_id).execute()
    paths = _flatten_image_paths(result.data or [])
    if not paths:
        return

    settings = get_settings()
    await supabase.storage.from_(settings.supabase_storage_bucket).remove(paths)
    logger.info("Removed %d storage objects during account purge user_prefix=%s", len(paths), user_id[:8])


def _flatten_image_paths(rows: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for row in rows:
        raw_paths = row.get("image_paths") or []
        if not isinstance(raw_paths, list):
            continue
        for raw_path in raw_paths:
            if not isinstance(raw_path, str) or not raw_path or raw_path in seen:
                continue
            seen.add(raw_path)
            paths.append(raw_path)
    return paths


async def _delete_revenuecat_subscriber(user_id: str) -> None:
    settings = get_settings()
    if not settings.revenuecat_api_key:
        return

    url = f"{settings.revenuecat_api_base_url.rstrip('/')}/subscribers/{user_id}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.delete(
            url,
            headers={"Authorization": f"Bearer {settings.revenuecat_api_key}"},
        )

    if response.status_code >= 400:
        logger.warning(
            "RevenueCat subscriber purge returned status=%d user_prefix=%s",
            response.status_code,
            user_id[:8],
        )


async def _delete_auth_user(user_id: str) -> None:
    supabase_admin = await get_supabase_admin()
    await supabase_admin.auth.admin.delete_user(user_id)
