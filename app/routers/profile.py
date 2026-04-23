import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.middleware.auth import get_current_user_id
from app.models.schemas import ProfileResponse, ProfileUpdate
from app.services.rag import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/profile", tags=["profile"])


async def _ensure_profile(user_id: UUID) -> dict:
    """Fetch profile, creating a default row if missing (defensive — trigger may not have run)."""
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
            }
        )
        .execute()
    )
    if not created.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to bootstrap profile"
        )
    return created.data[0]


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

    # Ensure profile exists before update
    await _ensure_profile(user_id)

    supabase = await get_supabase()
    result = await supabase.table("profiles").update(updates).eq("id", str(user_id)).execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    logger.info("Profile updated user_id=%s fields=%s", user_id, list(updates.keys()))
    return ProfileResponse.model_validate(result.data[0])
