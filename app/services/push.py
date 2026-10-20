from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.services.rag import get_supabase

logger = logging.getLogger(__name__)


async def send_tracking_push(
    user_id: str,
    tracking_type: str,
    identifier: str,
    status: str,
    title: str,
    body: str,
) -> int:
    """Send a localized Expo push for a generic tracking status change."""
    tokens = await _push_tokens_for_user(user_id)
    if not tokens:
        logger.info("No push token for tracking user_id=%s type=%s", user_id, tracking_type)
        return 0

    settings = get_settings()
    messages = [
        {
            "to": token,
            "title": title,
            "body": body,
            "sound": "default",
            "channelId": "status-tracking",
            "data": {
                "type": "tracking_status_change",
                "tracking_type": tracking_type,
                "identifier": identifier,
                "status": status,
            },
        }
        for token in tokens
    ]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                settings.expo_push_api_url,
                json=messages,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
    except httpx.HTTPError:
        logger.exception(
            "Expo tracking push failed user_id=%s type=%s status=%s", user_id, tracking_type, status
        )
        return 0

    return len(messages)


async def _push_tokens_for_user(user_id: str) -> list[str]:
    supabase = await get_supabase()
    profile_res = (
        await supabase.table("profiles")
        .select("id,expo_push_token,deletion_status")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    tokens: list[str] = []
    profile = profile_res.data[0] if profile_res.data else None
    # Accounts inside the deletion grace period must not receive pushes:
    # push_tokens rows stay enabled for a clean restore, so filter here.
    if profile and profile.get("deletion_status") == "pending":
        return []
    if profile:
        _append_token(tokens, profile.get("expo_push_token"))

    token_res = (
        await supabase.table("push_tokens")
        .select("expo_push_token")
        .eq("user_id", user_id)
        .eq("enabled", True)
        .execute()
    )
    for row in token_res.data or []:
        _append_token(tokens, row.get("expo_push_token"))
    return tokens


def _append_token(tokens: list[str], value: Any) -> None:
    if not isinstance(value, str):
        return
    token = value.strip()
    if not token.startswith(("ExponentPushToken[", "ExpoPushToken[")):
        return
    if token not in tokens:
        tokens.append(token)
