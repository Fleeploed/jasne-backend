from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.middleware.auth import get_current_user_id
from app.models.schemas import TrackingCreate, TrackingResponse
from app.services.oamp_client import check_status
from app.services.rag import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tracking/applications", tags=["tracking"])

MAX_ACTIVE_TRACKINGS = 5
INITIAL_NEXT_CHECK_DELAY = timedelta(days=1)
FINAL_STATUSES = {"schvaleno", "zamitnuto"}


@router.post("", response_model=TrackingResponse, status_code=status.HTTP_201_CREATED)
async def create_tracking(
    body: TrackingCreate,
    user_id: UUID = Depends(get_current_user_id),
) -> TrackingResponse:
    supabase = await get_supabase()
    user_id_str = str(user_id)

    profile = await _profile_for_user(supabase, user_id_str)
    if profile.get("plan") != "pro":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="OAMP status tracking is a Pro feature",
        )

    active_rows = await _active_trackings_for_user(supabase, user_id_str)
    if any(row.get("cislo_jednaci") == body.cislo_jednaci for row in active_rows):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Application is already tracked")
    if len(active_rows) >= MAX_ACTIVE_TRACKINGS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Active tracking limit reached",
        )

    now = datetime.now(UTC)
    latest_status = await _latest_status_for_case(supabase, user_id_str, body.cislo_jednaci)
    if latest_status is None:
        current_status = await check_status(body.cislo_jednaci)
        last_checked_at = now.isoformat()
    else:
        current_status = latest_status
        last_checked_at = None

    payload = {
        "user_id": user_id_str,
        "cislo_jednaci": body.cislo_jednaci,
        "current_status": current_status,
        "last_checked_at": last_checked_at,
        "next_check_at": (now + INITIAL_NEXT_CHECK_DELAY).isoformat(),
        "is_active": current_status not in FINAL_STATUSES,
        "notification_count": 0,
        "consecutive_not_found_count": 1 if current_status == "not_found" else 0,
        "consecutive_error_count": 1 if current_status == "error" else 0,
    }

    result = await supabase.table("tracked_applications").insert(payload).execute()
    rows = result.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create tracking",
        )

    logger.info(
        "Created OAMP tracking id=%s user_id=%s status=%s", rows[0].get("id"), user_id, current_status
    )
    return TrackingResponse.model_validate(rows[0])


@router.get("", response_model=list[TrackingResponse])
async def list_trackings(
    user_id: UUID = Depends(get_current_user_id),
) -> list[TrackingResponse]:
    supabase = await get_supabase()
    result = (
        await supabase.table("tracked_applications")
        .select("*")
        .eq("user_id", str(user_id))
        .eq("is_active", True)
        .order("created_at", desc=True)
        .execute()
    )
    return [TrackingResponse.model_validate(row) for row in result.data or []]


@router.delete("/{tracking_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tracking(
    tracking_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
) -> Response:
    supabase = await get_supabase()
    result = (
        await supabase.table("tracked_applications")
        .update({"is_active": False})
        .eq("id", str(tracking_id))
        .eq("user_id", str(user_id))
        .eq("is_active", True)
        .execute()
    )
    if not (result.data or []):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracking not found")
    logger.info("Soft-deleted OAMP tracking id=%s user_id=%s", tracking_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _profile_for_user(supabase, user_id: str) -> dict:
    result = await supabase.table("profiles").select("id,plan,language").eq("id", user_id).limit(1).execute()
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return rows[0]


async def _active_trackings_for_user(supabase, user_id: str) -> list[dict]:
    result = (
        await supabase.table("tracked_applications")
        .select("id,cislo_jednaci")
        .eq("user_id", user_id)
        .eq("is_active", True)
        .execute()
    )
    return result.data or []


async def _latest_status_for_case(supabase, user_id: str, cislo_jednaci: str) -> str | None:
    result = (
        await supabase.table("tracked_applications")
        .select("current_status,last_checked_at,created_at")
        .eq("user_id", user_id)
        .eq("cislo_jednaci", cislo_jednaci)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return None
    status_value = rows[0].get("current_status")
    return (
        status_value if status_value in {"probiha", "schvaleno", "zamitnuto", "not_found", "error"} else None
    )
