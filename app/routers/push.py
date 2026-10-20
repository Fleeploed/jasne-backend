import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.middleware.auth import get_active_user_id
from app.models.schemas import PushTokenCreate, PushTokenResponse
from app.services.rag import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/push-tokens", tags=["push"])


@router.post("", response_model=PushTokenResponse, status_code=status.HTTP_201_CREATED)
async def register_push_token(
    body: PushTokenCreate,
    user_id: UUID = Depends(get_active_user_id),
) -> PushTokenResponse:
    supabase = await get_supabase()
    now = datetime.now(UTC).isoformat()
    payload = body.model_dump(mode="json")
    payload.update(
        {
            "user_id": str(user_id),
            "enabled": True,
            "last_seen_at": now,
        }
    )

    result = (
        await supabase.table("push_tokens").upsert(payload, on_conflict="user_id,expo_push_token").execute()
    )
    await (
        supabase.table("profiles")
        .update({"expo_push_token": body.expo_push_token})
        .eq("id", str(user_id))
        .execute()
    )
    rows = result.data or []
    if not rows:
        fallback = (
            await supabase.table("push_tokens")
            .select("*")
            .eq("user_id", str(user_id))
            .eq("expo_push_token", body.expo_push_token)
            .limit(1)
            .execute()
        )
        rows = fallback.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register push token",
        )

    logger.info("Push token registered user_id=%s platform=%s", user_id, body.platform)
    return PushTokenResponse.model_validate(rows[0])


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable_push_token(
    token_id: UUID,
    user_id: UUID = Depends(get_active_user_id),
) -> None:
    supabase = await get_supabase()
    await (
        supabase.table("push_tokens")
        .update({"enabled": False})
        .eq("id", str(token_id))
        .eq("user_id", str(user_id))
        .execute()
    )
    logger.info("Push token disabled token_id=%s user_id=%s", token_id, user_id)
