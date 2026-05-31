from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.middleware.auth import get_current_user_id
from app.models.schemas import TrackingCreate, TrackingResponse
from app.services.rag import get_supabase
from app.services.trackers.base import CheckResult
from app.services.trackers.registry import get_tracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trackings", tags=["trackings"])

MAX_ACTIVE_TRACKINGS_PER_TYPE = 5


@router.post("", response_model=TrackingResponse, status_code=status.HTTP_201_CREATED)
async def create_tracking(
    body: TrackingCreate,
    response: Response,
    user_id: UUID = Depends(get_current_user_id),
) -> TrackingResponse:
    tracker = _tracker_or_422(body.tracking_type)
    identifier = tracker.normalize_identifier(body.identifier)
    if not tracker.validate_identifier(identifier):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid identifier")

    supabase = await get_supabase()
    user_id_str = str(user_id)

    profile = await _profile_for_user(supabase, user_id_str)
    if profile.get("plan") != "pro":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Background tracking is a Pro feature",
        )

    active_rows = await _active_trackings_for_user(supabase, user_id_str, body.tracking_type)
    existing = next((row for row in active_rows if row.get("identifier") == identifier), None)
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return TrackingResponse.model_validate(existing)
    if len(active_rows) >= MAX_ACTIVE_TRACKINGS_PER_TYPE:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Active tracking limit reached",
        )

    now = datetime.now(UTC)
    initial_status = tracker.initial_status_from_metadata(body.metadata)
    check = (
        CheckResult(status=initial_status, raw_data={})
        if initial_status is not None
        else await tracker.check(identifier, body.metadata)
    )
    metadata = _merge_metadata(body.metadata, check.raw_data)
    draft_tracking = {
        "tracking_type": body.tracking_type,
        "identifier": identifier,
        "current_status": check.status,
        "metadata": metadata,
        "created_at": now.isoformat(),
    }
    payload = {
        "user_id": user_id_str,
        "tracking_type": body.tracking_type,
        "identifier": identifier,
        "current_status": check.status,
        "metadata": metadata,
        "last_checked_at": now.isoformat(),
        "next_check_at": (now + tracker.get_next_check_interval(draft_tracking)).isoformat(),
        "is_active": not tracker.is_terminal_status(check.status),
        "notification_count": 0,
    }

    result = await supabase.table("trackings").insert(payload).execute()
    rows = result.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create tracking",
        )

    logger.info(
        "Created tracking id=%s type=%s user_id=%s status=%s",
        rows[0].get("id"),
        body.tracking_type,
        user_id,
        check.status,
    )
    return TrackingResponse.model_validate(rows[0])


@router.get("", response_model=list[TrackingResponse])
async def list_trackings(
    tracking_type: str | None = Query(default=None, alias="type"),
    user_id: UUID = Depends(get_current_user_id),
) -> list[TrackingResponse]:
    supabase = await get_supabase()
    query = (
        supabase.table("trackings")
        .select("*")
        .eq("user_id", str(user_id))
        .eq("is_active", True)
        .order("created_at", desc=True)
    )
    if tracking_type:
        query = query.eq("tracking_type", tracking_type.strip().lower())
    result = await query.execute()
    return [TrackingResponse.model_validate(row) for row in result.data or []]


@router.get("/{tracking_id}", response_model=TrackingResponse)
async def get_tracking(
    tracking_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
) -> TrackingResponse:
    supabase = await get_supabase()
    result = (
        await supabase.table("trackings")
        .select("*")
        .eq("id", str(tracking_id))
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracking not found")
    return TrackingResponse.model_validate(rows[0])


@router.delete("/{tracking_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tracking(
    tracking_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
) -> Response:
    supabase = await get_supabase()
    result = (
        await supabase.table("trackings")
        .update({"is_active": False})
        .eq("id", str(tracking_id))
        .eq("user_id", str(user_id))
        .eq("is_active", True)
        .execute()
    )
    if not (result.data or []):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracking not found")
    logger.info("Soft-deleted tracking id=%s user_id=%s", tracking_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _profile_for_user(supabase, user_id: str) -> dict:
    result = await supabase.table("profiles").select("id,plan,language").eq("id", user_id).limit(1).execute()
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return rows[0]


async def _active_trackings_for_user(supabase, user_id: str, tracking_type: str) -> list[dict]:
    result = (
        await supabase.table("trackings")
        .select("*")
        .eq("user_id", user_id)
        .eq("tracking_type", tracking_type)
        .eq("is_active", True)
        .execute()
    )
    return result.data or []


def _tracker_or_422(tracking_type: str):
    try:
        return get_tracker(tracking_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _merge_metadata(metadata: dict[str, Any] | None, raw_data: dict[str, Any]) -> dict[str, Any]:
    merged = dict(metadata or {})
    merged.update(raw_data)
    return merged
