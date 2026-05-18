"""Server-to-server webhooks."""

from __future__ import annotations

import logging
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, status

from app.config import get_settings
from app.services.revenuecat import (
    SubscriptionState,
    apply_subscription_state,
    event_already_processed,
    fetch_subscription_state,
    is_test_event,
    is_transfer_event,
    record_event,
    state_from_webhook_event,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _parse_user_id(app_user_id: str | None) -> UUID | None:
    if not app_user_id:
        return None
    try:
        return UUID(str(app_user_id))
    except ValueError:
        return None


@router.post("/revenuecat", status_code=status.HTTP_200_OK)
async def revenuecat_webhook(  # noqa: C901
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    settings = get_settings()

    # ---------- 1. Auth ----------
    if not settings.revenuecat_webhook_auth:
        logger.error("RevenueCat webhook rejected: REVENUECAT_WEBHOOK_AUTH is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RevenueCat webhook is not configured",
        )
    if authorization != settings.revenuecat_webhook_auth:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook auth")

    payload = await request.json()
    event = payload.get("event") or {}
    event_id = event.get("id")
    event_type = str(event.get("type") or "").upper()
    app_user_id = event.get("app_user_id") or event.get("original_app_user_id")

    # ---------- 2. TEST events: respond 200, no processing ----------
    if is_test_event(event_type):
        logger.info("RevenueCat TEST event received event_id=%s", event_id)
        return {"status": "ok", "reason": "test_event"}

    # ---------- 3. Deduplication ----------
    if event_id and await event_already_processed(event_id):
        logger.info(
            "RevenueCat webhook duplicate ignored event_id=%s type=%s",
            event_id,
            event_type,
        )
        return {"status": "ok", "reason": "duplicate"}

    # ---------- 4. TRANSFER: subscription moved to a different app_user_id ----------
    # Body shape: { transferred_from: [...uuids], transferred_to: [...uuids] }
    # Old account loses Pro, new account gains it (resolved via REST sync).
    if is_transfer_event(event_type):
        transferred_from = event.get("transferred_from") or []
        transferred_to = event.get("transferred_to") or []
        for raw_id in transferred_from:
            uid = _parse_user_id(raw_id)
            if uid:
                await apply_subscription_state(uid, SubscriptionState(plan="free"))
        for raw_id in transferred_to:
            uid = _parse_user_id(raw_id)
            if not uid:
                continue
            try:
                state = await fetch_subscription_state(str(uid))
            except (RuntimeError, httpx.HTTPError):
                # No REST? Best we can do is grant pro; next webhook will correct.
                state = SubscriptionState(plan="pro")
            await apply_subscription_state(uid, state)

        if event_id:
            await record_event(
                event_id=event_id,
                event_type=event_type,
                user_id=None,
                app_user_id=None,
                raw_payload=payload,
                resulting_plan=None,
            )
        return {
            "status": "ok",
            "transferred_from": str(len(transferred_from)),
            "transferred_to": str(len(transferred_to)),
        }

    # ---------- 5. Standard events ----------
    if not app_user_id:
        logger.warning(
            "RevenueCat webhook missing app_user_id event_id=%s type=%s",
            event_id,
            event_type,
        )
        return {"status": "ignored", "reason": "missing_app_user_id"}

    user_id = _parse_user_id(app_user_id)
    if user_id is None:
        logger.warning("RevenueCat webhook app_user_id is not a UUID: %s", app_user_id)
        return {"status": "ignored", "reason": "app_user_id_not_uuid"}

    # ---------- 6. Resolve canonical state ----------
    # Prefer REST (idempotent, ordering-resilient). Fall back to event parsing
    # when REST is unavailable or fails — webhook is at-least-once, the next
    # event will eventually correct any drift.
    try:
        state = await fetch_subscription_state(str(user_id))
    except RuntimeError:
        state = state_from_webhook_event(event, settings.revenuecat_entitlement_id)
        logger.warning(
            "RevenueCat REST sync disabled; inferred state=%s from event_id=%s type=%s",
            state,
            event_id,
            event_type,
        )
    except httpx.HTTPError as exc:
        logger.exception("RevenueCat REST sync failed event_id=%s user_id=%s", event_id, user_id)
        # Return 502 so RevenueCat retries the webhook with exponential backoff.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"RevenueCat sync failed: {type(exc).__name__}",
        ) from exc

    # ---------- 7. Persist + audit ----------
    await apply_subscription_state(user_id, state)
    if event_id:
        await record_event(
            event_id=event_id,
            event_type=event_type,
            user_id=user_id,
            app_user_id=str(app_user_id),
            raw_payload=payload,
            resulting_plan=state.plan,
        )

    return {"status": "ok", "plan": state.plan}
