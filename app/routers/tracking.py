from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.middleware.auth import get_active_user_id
from app.models.schemas import TrackingCreate, TrackingResponse
from app.services.rag import get_supabase
from app.services.trackers.base import BaseTracker, CheckResult
from app.services.trackers.registry import get_tracker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trackings", tags=["trackings"])

MAX_ACTIVE_TRACKINGS_PER_TYPE = 1


@router.post("", response_model=TrackingResponse, status_code=status.HTTP_201_CREATED)
async def create_tracking(
    body: TrackingCreate,
    response: Response,
    user_id: UUID = Depends(get_active_user_id),
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

    # Synchronous first check used to block the response for tens of seconds
    # (Playwright + OAMP page load), which made the UI feel like nothing
    # happened until the whole probe finished. Instead we create the row
    # immediately with a placeholder "probiha" status when no trusted hint is
    # available, and run the real first check in the background.
    if initial_status is not None:
        check = CheckResult(status=initial_status, raw_data={})
        run_background_check = False
    else:
        check = CheckResult(status="probiha", raw_data={})
        run_background_check = True

    metadata = _merge_metadata(body.metadata, check.raw_data)
    draft_tracking = {
        "tracking_type": body.tracking_type,
        "identifier": identifier,
        "current_status": check.status,
        "metadata": metadata,
        "created_at": now.isoformat(),
    }
    # When we still need to do the first probe, schedule the next check window
    # immediately so the periodic cron picks it up if the background task is
    # killed (e.g. container redeploy before it finishes).
    next_check_at = now if run_background_check else now + tracker.get_next_check_interval(draft_tracking)
    payload = {
        "user_id": user_id_str,
        "tracking_type": body.tracking_type,
        "identifier": identifier,
        "current_status": check.status,
        "metadata": metadata,
        # last_checked_at stays null until the first probe really lands.
        "last_checked_at": None if run_background_check else now.isoformat(),
        "next_check_at": next_check_at.isoformat(),
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

    created = rows[0]
    logger.info(
        "Created tracking id=%s type=%s user_id=%s status=%s background=%s",
        created.get("id"),
        body.tracking_type,
        user_id,
        check.status,
        run_background_check,
    )

    if run_background_check:
        # Fire-and-forget. Errors are swallowed inside the task; the cron will
        # retry from `next_check_at = now`.
        asyncio.create_task(
            _run_first_check(
                tracking_id=UUID(str(created["id"])),
                user_id=user_id_str,
                tracker=tracker,
                identifier=identifier,
                metadata=body.metadata,
            )
        )

    return TrackingResponse.model_validate(created)


@router.get("", response_model=list[TrackingResponse])
async def list_trackings(
    tracking_type: str | None = Query(default=None, alias="type"),
    user_id: UUID = Depends(get_active_user_id),
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
    user_id: UUID = Depends(get_active_user_id),
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


@router.post("/{tracking_id}/check", response_model=TrackingResponse)
async def check_tracking_now(
    tracking_id: UUID,
    user_id: UUID = Depends(get_active_user_id),
) -> TrackingResponse:
    supabase = await get_supabase()
    row = await _active_tracking_for_user(supabase, str(user_id), tracking_id)
    tracker = _tracker_or_422(str(row.get("tracking_type") or ""))
    now = datetime.now(UTC)

    try:
        check = await tracker.check(str(row["identifier"]), row.get("metadata") or {})
    except Exception as exc:
        logger.warning("Manual tracking check failed id=%s type=%s", tracking_id, row.get("tracking_type"))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not check tracking right now",
        ) from exc

    metadata = _merge_metadata(row.get("metadata") or {}, check.raw_data)
    projected_tracking = {
        **row,
        "current_status": check.status,
        "metadata": metadata,
        "last_checked_at": now.isoformat(),
    }
    update = {
        "current_status": check.status,
        "metadata": metadata,
        "last_checked_at": now.isoformat(),
        "next_check_at": (now + tracker.get_next_check_interval(projected_tracking)).isoformat(),
        "is_active": not tracker.is_terminal_status(check.status),
    }
    result = (
        await supabase.table("trackings")
        .update(update)
        .eq("id", str(tracking_id))
        .eq("user_id", str(user_id))
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update tracking",
        )

    logger.info(
        "Manual tracking check id=%s type=%s status=%s",
        tracking_id,
        row.get("tracking_type"),
        check.status,
    )
    return TrackingResponse.model_validate(rows[0])


@router.delete("/{tracking_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tracking(
    tracking_id: UUID,
    user_id: UUID = Depends(get_active_user_id),
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


async def _active_tracking_for_user(supabase, user_id: str, tracking_id: UUID) -> dict:
    result = (
        await supabase.table("trackings")
        .select("*")
        .eq("id", str(tracking_id))
        .eq("user_id", user_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tracking not found")
    return rows[0]


def _tracker_or_422(tracking_type: str):
    try:
        return get_tracker(tracking_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


def _merge_metadata(metadata: dict[str, Any] | None, raw_data: dict[str, Any]) -> dict[str, Any]:
    merged = dict(metadata or {})
    merged.update(raw_data)
    return merged


_FIRST_CHECK_TIMEOUT_SECONDS = 90.0


async def _run_first_check(
    tracking_id: UUID,
    user_id: str,
    tracker: BaseTracker,
    identifier: str,
    metadata: dict[str, Any],
) -> None:
    """Run the first tracker probe in the background and persist the result.

    Always writes back to the row — even on failure — so the frontend can't
    get stuck on a "Проверяем…" UI when last_checked_at never flips. A
    failure path lands `status="error"` + a populated `last_checked_at`; the
    periodic cron will retry from `next_check_at`.
    """
    try:
        check = await asyncio.wait_for(
            tracker.check(identifier, metadata),
            timeout=_FIRST_CHECK_TIMEOUT_SECONDS,
        )
    except (TimeoutError, Exception) as exc:
        logger.exception(
            "First tracking check failed in background id=%s: %s",
            tracking_id,
            exc,
        )
        # Make sure the UI sees *something* — otherwise the row stays at
        # last_checked_at=null forever and the spinner is stuck.
        await _persist_first_check_failure(tracking_id, user_id, tracker)
        return

    try:
        supabase = await get_supabase()
        now = datetime.now(UTC)
        row = (
            await supabase.table("trackings")
            .select("*")
            .eq("id", str(tracking_id))
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        existing = (row.data or [None])[0]
        if existing is None:
            logger.warning("Tracking %s vanished before background check could write back", tracking_id)
            return

        merged_metadata = _merge_metadata(existing.get("metadata") or {}, check.raw_data)
        projected = {
            **existing,
            "current_status": check.status,
            "metadata": merged_metadata,
            "last_checked_at": now.isoformat(),
        }
        update = {
            "current_status": check.status,
            "metadata": merged_metadata,
            "last_checked_at": now.isoformat(),
            "next_check_at": (now + tracker.get_next_check_interval(projected)).isoformat(),
            "is_active": not tracker.is_terminal_status(check.status),
        }
        await (
            supabase.table("trackings")
            .update(update)
            .eq("id", str(tracking_id))
            .eq("user_id", user_id)
            .execute()
        )
        logger.info(
            "Background first-check landed id=%s status=%s",
            tracking_id,
            check.status,
        )
    except Exception:
        logger.exception("Failed to persist background first-check id=%s", tracking_id)


async def _persist_first_check_failure(
    tracking_id: UUID,
    user_id: str,
    tracker: BaseTracker,
) -> None:
    """Mark a tracking as errored so the UI exits the "Проверяем…" state.

    Keeps the row active and pushes `next_check_at` forward by an
    error-appropriate interval so the cron will retry.
    """
    try:
        supabase = await get_supabase()
        now = datetime.now(UTC)
        projected = {"current_status": "error"}
        await (
            supabase.table("trackings")
            .update(
                {
                    "current_status": "error",
                    "last_checked_at": now.isoformat(),
                    "next_check_at": (now + tracker.get_next_check_interval(projected)).isoformat(),
                    "is_active": True,
                }
            )
            .eq("id", str(tracking_id))
            .eq("user_id", user_id)
            .execute()
        )
    except Exception:
        logger.exception("Failed to persist first-check failure id=%s", tracking_id)


# Re-exported for tests that want to assert on the type. Re-export avoids a
# circular import when conftest patches `_run_first_check`.
__all__ = ["router", "_run_first_check", "MAX_ACTIVE_TRACKINGS_PER_TYPE", "timedelta"]
