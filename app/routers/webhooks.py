"""Server-to-server webhooks."""

from __future__ import annotations

import logging
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, status

from app.config import get_settings
from app.services.revenuecat import fetch_revenuecat_plan, plan_from_webhook_event, set_profile_plan

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/revenuecat", status_code=status.HTTP_200_OK)
async def revenuecat_webhook(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    settings = get_settings()
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
    app_user_id = event.get("app_user_id") or event.get("original_app_user_id")
    event_id = event.get("id")
    event_type = event.get("type")

    if not app_user_id:
        logger.warning("RevenueCat webhook missing app_user_id event_id=%s type=%s", event_id, event_type)
        return {"status": "ignored", "reason": "missing_app_user_id"}

    try:
        user_id = UUID(str(app_user_id))
    except ValueError:
        logger.warning("RevenueCat webhook app_user_id is not a UUID: %s", app_user_id)
        return {"status": "ignored", "reason": "app_user_id_not_uuid"}

    try:
        plan = await fetch_revenuecat_plan(str(user_id))
    except RuntimeError:
        plan = plan_from_webhook_event(event, settings.revenuecat_entitlement_id)
        logger.warning(
            "RevenueCat REST sync disabled; inferred plan=%s from event_id=%s type=%s",
            plan,
            event_id,
            event_type,
        )
    except httpx.HTTPError as exc:
        logger.exception("RevenueCat REST sync failed event_id=%s user_id=%s", event_id, user_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"RevenueCat sync failed: {type(exc).__name__}",
        ) from exc

    await set_profile_plan(user_id, plan)
    return {"status": "ok", "plan": plan}
