import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.middleware.auth import get_current_user_id
from app.models.schemas import DeadlineCreate, DeadlineResponse, DeadlineUpdate
from app.services.rag import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/deadlines", tags=["deadlines"])


@router.get("", response_model=list[DeadlineResponse])
async def list_deadlines(
    only_active: bool = True,
    user_id: UUID = Depends(get_current_user_id),
) -> list[DeadlineResponse]:
    supabase = await get_supabase()
    query = supabase.table("deadlines").select("*").eq("user_id", str(user_id))
    if only_active:
        query = query.eq("status", "active")
    result = await query.order("due_date").execute()
    return [DeadlineResponse.model_validate(row) for row in (result.data or [])]


@router.get("/{deadline_id}", response_model=DeadlineResponse)
async def get_deadline(
    deadline_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
) -> DeadlineResponse:
    supabase = await get_supabase()
    result = (
        await supabase.table("deadlines")
        .select("*")
        .eq("id", str(deadline_id))
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deadline not found")
    return DeadlineResponse.model_validate(result.data[0])


@router.post("", response_model=DeadlineResponse, status_code=status.HTTP_201_CREATED)
async def create_deadline(
    body: DeadlineCreate,
    user_id: UUID = Depends(get_current_user_id),
) -> DeadlineResponse:
    supabase = await get_supabase()
    payload = body.model_dump(mode="json")
    payload["user_id"] = str(user_id)

    result = await supabase.table("deadlines").insert(payload).execute()
    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create deadline"
        )

    logger.info("Deadline created user_id=%s due_date=%s", user_id, body.due_date)
    return DeadlineResponse.model_validate(result.data[0])


@router.patch("/{deadline_id}", response_model=DeadlineResponse)
async def update_deadline(
    deadline_id: UUID,
    body: DeadlineUpdate,
    user_id: UUID = Depends(get_current_user_id),
) -> DeadlineResponse:
    updates = body.model_dump(exclude_none=True, mode="json")
    if not updates:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No fields to update")

    supabase = await get_supabase()
    result = (
        await supabase.table("deadlines")
        .update(updates)
        .eq("id", str(deadline_id))
        .eq("user_id", str(user_id))
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deadline not found")

    logger.info("Deadline updated deadline_id=%s fields=%s", deadline_id, list(updates.keys()))
    return DeadlineResponse.model_validate(result.data[0])


@router.delete("/{deadline_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deadline(
    deadline_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
) -> None:
    supabase = await get_supabase()
    await (
        supabase.table("deadlines").delete().eq("id", str(deadline_id)).eq("user_id", str(user_id)).execute()
    )
    logger.info("Deadline deleted deadline_id=%s user_id=%s", deadline_id, user_id)
