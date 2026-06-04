"""GET / PUT / DELETE /profile — fetch, update, and soft-delete user profile.

Profile is auto-created by a Postgres trigger on auth signup, but we have a
defensive fallback that creates it on first read in case the trigger missed.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.middleware.auth import get_current_user_id
from app.models.schemas import ProfileDeletionStatusResponse, ProfileResponse, ProfileUpdate
from app.services.account_deletion import (
    build_pending_deletion_update,
    build_restore_update,
    is_pending_deletion,
)
from app.services.rag import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/profile", tags=["profile"])


async def _ensure_profile(user_id: UUID) -> dict:
    """Fetch profile, creating a default row if missing.

    Defensive — the auth-trigger should always create one, but if it didn't
    run (replication lag, manual user insert, etc.) we don't want the app
    to 404 forever.
    """
    supabase = await get_supabase()
    result = await supabase.table("profiles").select("*").eq("id", str(user_id)).limit(1).execute()
    if result.data:
        return result.data[0]

    logger.warning("Profile missing for user_id=%s, creating default row", user_id)
    created = (
        await supabase.table("profiles")
        .insert(
            {
                "id": str(user_id),
                "language": "cs",
                "has_datova_schranka": False,
                "plan": "free",
                "deletion_status": "active",
            }
        )
        .execute()
    )
    if not created.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to bootstrap profile",
        )
    return created.data[0]


@router.get("/deletion-status", response_model=ProfileDeletionStatusResponse)
async def get_profile_deletion_status(
    user_id: UUID = Depends(get_current_user_id),
) -> ProfileDeletionStatusResponse:
    row = await _ensure_profile(user_id)
    return ProfileDeletionStatusResponse.model_validate(row)


@router.get("", response_model=ProfileResponse)
async def get_profile(user_id: UUID = Depends(get_current_user_id)) -> ProfileResponse:
    row = await _ensure_profile(user_id)
    return ProfileResponse.model_validate(row)


@router.put("", response_model=ProfileResponse)
async def update_profile(
    body: ProfileUpdate,
    user_id: UUID = Depends(get_current_user_id),
) -> ProfileResponse:
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No fields to update")

    # `plan` is intentionally NOT in ProfileUpdate — only the RevenueCat
    # webhook (server-to-server) is allowed to flip it. Strip defensively
    # in case schema drift ever lets it through.
    updates.pop("plan", None)

    profile = await _ensure_profile(user_id)
    if is_pending_deletion(profile):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "account_pending_deletion",
                "message": "Restore the account before updating profile settings.",
                "deletion_scheduled_at": profile.get("deletion_scheduled_at"),
            },
        )

    supabase = await get_supabase()
    result = await supabase.table("profiles").update(updates).eq("id", str(user_id)).execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    logger.info("Profile updated user_id=%s fields=%s", user_id, list(updates.keys()))
    return ProfileResponse.model_validate(result.data[0])


@router.post("/restore", response_model=ProfileResponse)
async def restore_profile(user_id: UUID = Depends(get_current_user_id)) -> ProfileResponse:
    """Restore an account that is inside the 7-day deletion grace period."""
    profile = await _ensure_profile(user_id)
    if not is_pending_deletion(profile):
        return ProfileResponse.model_validate(profile)

    supabase = await get_supabase()
    result = await supabase.table("profiles").update(build_restore_update()).eq("id", str(user_id)).execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    logger.info("account_deletion_restored user_prefix=%s", str(user_id)[:8])
    return ProfileResponse.model_validate(result.data[0])


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_profile(
    reason: str | None = Query(default=None, max_length=80),
    user_id: UUID = Depends(get_current_user_id),
) -> Response:
    """Schedule account deletion with a 7-day restore window.

    Data, subscriptions, storage files, and the Supabase Auth identity are kept
    until the purge job runs after the grace period. This blocks immediate
    delete/recreate abuse while still letting the user restore their account.
    """
    user_id_str = str(user_id)
    supabase = await get_supabase()

    profile = await _ensure_profile(user_id)
    if is_pending_deletion(profile):
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    result = (
        await supabase.table("profiles")
        .update(build_pending_deletion_update(reason))
        .eq("id", user_id_str)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    if reason is not None:
        logger.info("account_deletion_scheduled reason=%s user_prefix=%s", reason, user_id_str[:8])
    else:
        logger.info("account_deletion_scheduled user_prefix=%s", user_id_str[:8])

    return Response(status_code=status.HTTP_204_NO_CONTENT)
