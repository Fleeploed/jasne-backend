"""GET / PUT / DELETE /profile — fetch, update, and delete user profile.

Profile is auto-created by a Postgres trigger on auth signup, but we have a
defensive fallback that creates it on first read in case the trigger missed.
"""

import logging
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.config import get_settings
from app.middleware.auth import get_current_user_id
from app.models.schemas import ProfileResponse, ProfileUpdate
from app.services.rag import get_supabase
from app.services.supabase_admin import get_supabase_admin

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

    await _ensure_profile(user_id)

    supabase = await get_supabase()
    result = await supabase.table("profiles").update(updates).eq("id", str(user_id)).execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")

    logger.info("Profile updated user_id=%s fields=%s", user_id, list(updates.keys()))
    return ProfileResponse.model_validate(result.data[0])


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_profile(
    reason: str | None = None,
    user_id: UUID = Depends(get_current_user_id),
) -> Response:
    """Hard-delete the authenticated user's account data and auth identity."""
    user_id_str = str(user_id)
    supabase = await get_supabase()

    try:
        await _delete_revenuecat_subscriber(user_id_str)
    except Exception:
        logger.warning("RevenueCat subscriber deletion failed user_id=%s", user_id, exc_info=True)

    try:
        await _delete_storage_objects(supabase, user_id_str)
    except Exception:
        logger.warning("Storage cleanup failed user_id=%s", user_id, exc_info=True)

    try:
        await supabase.table("deadlines").delete().eq("user_id", user_id_str).execute()
    except Exception:
        logger.warning("Deadline cleanup failed user_id=%s", user_id, exc_info=True)

    try:
        await supabase.table("usage_limits").delete().eq("user_id", user_id_str).execute()
    except Exception:
        logger.warning("Usage limit cleanup failed user_id=%s", user_id, exc_info=True)

    try:
        await _delete_scans(supabase, user_id_str)
    except Exception:
        logger.warning("Scan cleanup failed user_id=%s", user_id, exc_info=True)

    try:
        await supabase.table("profiles").delete().eq("id", user_id_str).execute()
    except Exception:
        logger.error("Profile row deletion failed user_id=%s", user_id, exc_info=True)

    try:
        await _delete_auth_user(user_id_str)
    except Exception as exc:
        logger.error("Auth user deletion failed user_id=%s", user_id, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Account data deleted but auth user removal failed. Contact support.",
        ) from exc

    if reason is not None:
        logger.info("account_deleted reason=%s user_prefix=%s", reason, user_id_str[:8])

    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _delete_revenuecat_subscriber(user_id: str) -> None:
    settings = get_settings()
    if not settings.revenuecat_api_key:
        logger.warning("Skipping RevenueCat subscriber deletion: REVENUECAT_API_KEY is not configured")
        return

    url = f"{settings.revenuecat_api_base_url.rstrip('/')}/subscribers/{user_id}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.delete(
            url,
            headers={"Authorization": f"Bearer {settings.revenuecat_api_key}"},
        )

    if response.status_code >= 400:
        logger.warning(
            "RevenueCat subscriber deletion returned status=%d user_prefix=%s",
            response.status_code,
            user_id[:8],
        )
        return

    logger.info(
        "RevenueCat subscriber deletion returned status=%d user_prefix=%s",
        response.status_code,
        user_id[:8],
    )


async def _delete_storage_objects(supabase: Any, user_id: str) -> None:
    result = await supabase.table("scans").select("image_paths").eq("user_id", user_id).execute()
    paths = _flatten_image_paths(result.data or [])
    if not paths:
        return

    settings = get_settings()
    await supabase.storage.from_(settings.supabase_storage_bucket).remove(paths)
    logger.info("Removed %d storage objects user_prefix=%s", len(paths), user_id[:8])


def _flatten_image_paths(rows: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for row in rows:
        raw_paths = row.get("image_paths") or []
        if not isinstance(raw_paths, list):
            continue
        for raw_path in raw_paths:
            if not isinstance(raw_path, str) or not raw_path or raw_path in seen:
                continue
            seen.add(raw_path)
            paths.append(raw_path)
    return paths


async def _delete_scans(supabase: Any, user_id: str) -> None:
    try:
        await supabase.rpc("delete_user_scans", {"p_user_id": user_id}).execute()
        return
    except Exception:
        logger.warning("delete_user_scans RPC failed; falling back to raw scan delete", exc_info=True)

    await supabase.table("scans").delete().eq("user_id", user_id).execute()


async def _delete_auth_user(user_id: str) -> None:
    supabase_admin = await get_supabase_admin()
    await supabase_admin.auth.admin.delete_user(user_id)
