"""RevenueCat subscription sync helpers.

RevenueCat webhooks are at-least-once delivery, so all operations here are
idempotent: fetch canonical subscriber state, then set the user's current plan.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx

from app.config import get_settings
from app.models.schemas import UserPlan
from app.services.rag import get_supabase

logger = logging.getLogger(__name__)

_ACTIVE_EVENT_TYPES = {
    "INITIAL_PURCHASE",
    "NON_RENEWING_PURCHASE",
    "RENEWAL",
    "UNCANCELLATION",
    "PRODUCT_CHANGE",
    "SUBSCRIPTION_EXTENDED",
    "TEMPORARY_ENTITLEMENT_GRANT",
}

_INACTIVE_EVENT_TYPES = {
    "EXPIRATION",
}


def _parse_revenuecat_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        logger.warning("Could not parse RevenueCat date: %s", value)
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _ms_to_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _is_future_or_lifetime(expires_at: datetime | None) -> bool:
    return expires_at is None or expires_at > datetime.now(UTC)


def plan_from_subscriber(subscriber: dict[str, Any], entitlement_id: str) -> UserPlan:
    """Return pro/free from RevenueCat's canonical subscriber shape."""
    entitlements = subscriber.get("entitlements") or {}
    entitlement = entitlements.get(entitlement_id) or {}
    expires_at = _parse_revenuecat_datetime(entitlement.get("expires_date"))
    return "pro" if entitlement and _is_future_or_lifetime(expires_at) else "free"


def plan_from_webhook_event(event: dict[str, Any], entitlement_id: str) -> UserPlan:
    """Conservative fallback when REST sync is not configured.

    CANCELLATION does not necessarily mean access ended, so it is intentionally
    not treated as free here.
    """
    entitlement_ids = event.get("entitlement_ids") or []
    if entitlement_id not in entitlement_ids and event.get("entitlement_id") != entitlement_id:
        return "free"

    event_type = str(event.get("type") or "").upper()
    expires_at = _ms_to_datetime(event.get("expiration_at_ms"))
    if event_type in _ACTIVE_EVENT_TYPES and _is_future_or_lifetime(expires_at):
        return "pro"
    if event_type in _INACTIVE_EVENT_TYPES:
        return "free"
    return "free"


async def fetch_revenuecat_plan(app_user_id: str) -> UserPlan:
    settings = get_settings()
    if not settings.revenuecat_api_key:
        raise RuntimeError("REVENUECAT_API_KEY is not configured")

    url = f"{settings.revenuecat_api_base_url.rstrip('/')}/subscribers/{app_user_id}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {settings.revenuecat_api_key}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()

    data = response.json()
    return plan_from_subscriber(data.get("subscriber") or {}, settings.revenuecat_entitlement_id)


async def set_profile_plan(user_id: UUID, plan: UserPlan) -> None:
    from app.routers.profile import _ensure_profile

    # The webhook may arrive before the Postgres auth trigger has created the
    # profile row. Bootstrap a default profile so the plan update lands instead
    # of silently being dropped.
    await _ensure_profile(user_id)

    supabase = await get_supabase()
    result = await supabase.table("profiles").update({"plan": plan}).eq("id", str(user_id)).execute()
    if not result.data:
        logger.error("RevenueCat webhook could not update profile user_id=%s plan=%s", user_id, plan)
        return
    logger.info("RevenueCat synced user_id=%s plan=%s", user_id, plan)
