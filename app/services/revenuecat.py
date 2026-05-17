"""RevenueCat subscription sync helpers.

RevenueCat webhooks are at-least-once delivery, so all operations here are
idempotent: fetch canonical subscriber state, then set the user's current
plan and subscription details.

Architecture:
    1. Webhook arrives at /webhooks/revenuecat
    2. Deduplicated by event_id (revenuecat_events table)
    3. REST API queried for canonical subscriber state (preferred)
       - If REST is disabled or fails, fall back to webhook event parsing
    4. profiles.plan + subscription_* fields updated atomically
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx

from app.config import get_settings
from app.models.schemas import UserPlan
from app.services.rag import get_supabase

logger = logging.getLogger(__name__)

# Webhook event types that imply ACTIVE access (when entitlement matches and
# expiration is in the future / absent).
_ACTIVE_EVENT_TYPES = {
    "INITIAL_PURCHASE",
    "NON_RENEWING_PURCHASE",
    "RENEWAL",
    "UNCANCELLATION",
    "PRODUCT_CHANGE",
    "SUBSCRIPTION_EXTENDED",
    "TEMPORARY_ENTITLEMENT_GRANT",
}

# Webhook event types that imply IMMEDIATE loss of access regardless of
# previous expiration date.
_INACTIVE_EVENT_TYPES = {
    "EXPIRATION",
    "REFUND",
    "SUBSCRIPTION_PAUSED",
}

# Events that need special handling (not simple plan flip).
_TRANSFER_EVENT_TYPES = {"TRANSFER"}
_TEST_EVENT_TYPES = {"TEST"}

# Events that DO NOT change plan state on their own:
# - CANCELLATION → user still has access until expires_at
# - BILLING_ISSUE → grace period (~16d on iOS), user still has access
# We let REST sync determine the true plan, or keep current plan if REST
# is unavailable.
_NEUTRAL_EVENT_TYPES = {"CANCELLATION", "BILLING_ISSUE"}


@dataclass(frozen=True)
class SubscriptionState:
    """Canonical subscription snapshot. Source of truth for profiles columns."""

    plan: UserPlan
    product_id: str | None = None
    expires_at: datetime | None = None
    will_renew: bool = False


# ---------------------------------------------------------------------------
# Datetime parsing
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# State extraction
# ---------------------------------------------------------------------------


def state_from_subscriber(subscriber: dict[str, Any], entitlement_id: str) -> SubscriptionState:
    """Extract canonical state from RevenueCat REST `/subscribers/{id}` response.

    This is the AUTHORITATIVE source — REST returns the current entitlement
    snapshot which already incorporates renewals, refunds, cancellations,
    grace periods, etc.
    """
    entitlements = subscriber.get("entitlements") or {}
    entitlement = entitlements.get(entitlement_id) or {}

    expires_at = _parse_revenuecat_datetime(entitlement.get("expires_date"))
    product_id = entitlement.get("product_identifier")

    # `unsubscribe_detected_at` tells us the user has cancelled (won't renew).
    # If absent → will renew automatically. Lifetime/non-subscription products
    # have no expires_date — treat as will_renew=False (one-time purchase).
    unsubscribed = bool(entitlement.get("unsubscribe_detected_at"))
    has_period = expires_at is not None
    will_renew = has_period and not unsubscribed

    is_active = bool(entitlement) and _is_future_or_lifetime(expires_at)
    plan: UserPlan = "pro" if is_active else "free"

    return SubscriptionState(
        plan=plan,
        product_id=product_id,
        expires_at=expires_at,
        will_renew=will_renew,
    )


def state_from_webhook_event(event: dict[str, Any], entitlement_id: str) -> SubscriptionState:
    """Conservative fallback when REST sync is unavailable.

    Returns best-effort state inferred from a single webhook event. Less
    accurate than REST because events can arrive out of order and don't
    always carry the full entitlement snapshot.
    """
    entitlement_ids = event.get("entitlement_ids") or []
    if entitlement_id not in entitlement_ids and event.get("entitlement_id") != entitlement_id:
        return SubscriptionState(plan="free")

    event_type = str(event.get("type") or "").upper()
    expires_at = _ms_to_datetime(event.get("expiration_at_ms"))
    product_id = event.get("product_id")
    # Webhook sometimes carries cancel_reason / period_type. We approximate
    # will_renew = (no cancel) AND (has expiration date).
    cancelled = bool(event.get("cancel_reason"))
    will_renew = expires_at is not None and not cancelled

    # Immediate-revoke events override expiration.
    if event_type in _INACTIVE_EVENT_TYPES:
        return SubscriptionState(plan="free", product_id=product_id)

    # Cancellation / billing issue: user keeps access until expires_at.
    # If expires_at is missing in the event (vs explicitly in the future),
    # be conservative and treat as inactive — REST sync will correct if the
    # user still has time. Webhook absence ≠ lifetime.
    if event_type in _NEUTRAL_EVENT_TYPES:
        if expires_at is None:
            return SubscriptionState(plan="free", product_id=product_id, will_renew=False)
        plan: UserPlan = "pro" if expires_at > datetime.now(UTC) else "free"
        return SubscriptionState(
            plan=plan,
            product_id=product_id,
            expires_at=expires_at,
            will_renew=False,  # by definition cancelled / billing problem
        )

    if event_type in _ACTIVE_EVENT_TYPES and _is_future_or_lifetime(expires_at):
        return SubscriptionState(
            plan="pro",
            product_id=product_id,
            expires_at=expires_at,
            will_renew=will_renew,
        )

    # Unknown event type — be conservative.
    return SubscriptionState(plan="free", product_id=product_id)


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------


async def fetch_subscription_state(app_user_id: str) -> SubscriptionState:
    """Fetch canonical subscriber state from RevenueCat REST API.

    Raises:
        RuntimeError: if REVENUECAT_API_KEY is not configured (caller should
            fall back to webhook event parsing).
        httpx.HTTPError: on network or 5xx failure.
    """
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
    return state_from_subscriber(data.get("subscriber") or {}, settings.revenuecat_entitlement_id)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def apply_subscription_state(user_id: UUID, state: SubscriptionState) -> None:
    """Write the canonical subscription state to profiles."""
    from app.routers.profile import _ensure_profile

    # Bootstrap profile if the auth trigger hasn't fired yet.
    await _ensure_profile(user_id)

    supabase = await get_supabase()
    updates: dict[str, Any] = {
        "plan": state.plan,
        "subscription_product_id": state.product_id,
        "subscription_expires_at": state.expires_at.isoformat() if state.expires_at else None,
        "subscription_will_renew": state.will_renew,
    }
    result = await supabase.table("profiles").update(updates).eq("id", str(user_id)).execute()
    if not result.data:
        logger.error(
            "RevenueCat sync could not update profile user_id=%s plan=%s",
            user_id,
            state.plan,
        )
        return

    logger.info(
        "RevenueCat synced user_id=%s plan=%s product=%s expires_at=%s will_renew=%s",
        user_id,
        state.plan,
        state.product_id,
        state.expires_at,
        state.will_renew,
    )


# ---------------------------------------------------------------------------
# Event log (dedup + audit)
# ---------------------------------------------------------------------------


async def event_already_processed(event_id: str) -> bool:
    """Return True if we've already seen this event_id."""
    supabase = await get_supabase()
    result = (
        await supabase.table("revenuecat_events")
        .select("event_id")
        .eq("event_id", event_id)
        .limit(1)
        .execute()
    )
    return bool(result.data)


async def record_event(
    *,
    event_id: str,
    event_type: str,
    user_id: UUID | None,
    app_user_id: str | None,
    raw_payload: dict[str, Any],
    resulting_plan: UserPlan | None,
) -> None:
    """Insert audit record. Safe to call even if event_id already exists
    (returns silently — the PK constraint protects us)."""
    supabase = await get_supabase()
    try:
        await supabase.table("revenuecat_events").insert(
            {
                "event_id": event_id,
                "event_type": event_type,
                "user_id": str(user_id) if user_id else None,
                "app_user_id": app_user_id,
                "raw_payload": raw_payload,
                "resulting_plan": resulting_plan,
            }
        ).execute()
    except Exception:  # noqa: BLE001 — log and continue, dedup is best-effort
        logger.warning("Failed to record revenuecat_event event_id=%s", event_id, exc_info=True)


# ---------------------------------------------------------------------------
# Backwards-compatible thin wrappers
# (kept so any existing callers don't break)
# ---------------------------------------------------------------------------


def plan_from_subscriber(subscriber: dict[str, Any], entitlement_id: str) -> UserPlan:
    return state_from_subscriber(subscriber, entitlement_id).plan


def plan_from_webhook_event(event: dict[str, Any], entitlement_id: str) -> UserPlan:
    return state_from_webhook_event(event, entitlement_id).plan


async def fetch_revenuecat_plan(app_user_id: str) -> UserPlan:
    state = await fetch_subscription_state(app_user_id)
    return state.plan


async def set_profile_plan(user_id: UUID, plan: UserPlan) -> None:
    await apply_subscription_state(user_id, SubscriptionState(plan=plan))


# ---------------------------------------------------------------------------
# Event-type helpers (re-exported for the webhook router)
# ---------------------------------------------------------------------------


def is_transfer_event(event_type: str) -> bool:
    return event_type.upper() in _TRANSFER_EVENT_TYPES


def is_test_event(event_type: str) -> bool:
    return event_type.upper() in _TEST_EVENT_TYPES
