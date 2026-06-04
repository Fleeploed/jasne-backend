"""GET /limits — current usage vs plan caps.

Mobile calls this before showing the camera (to gate "create scan") and
before enabling the chat input (to gate "ask question"). Saves a round-trip
of getting a 402 mid-action.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends

from app.middleware.auth import get_active_user_id
from app.models.schemas import LimitsResponse
from app.routers.profile import _ensure_profile
from app.services.limits import caps_for_plan, get_or_init_usage, usage_period_for_profile

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/limits", tags=["limits"])


@router.get("", response_model=LimitsResponse)
async def get_limits(
    scan_id: UUID | None = None,
    user_id: UUID = Depends(get_active_user_id),
) -> LimitsResponse:
    profile = await _ensure_profile(user_id)
    plan = profile.get("plan", "free")
    caps = caps_for_plan(plan)

    month = usage_period_for_profile(profile)
    usage = await get_or_init_usage(user_id, month)

    scans_used = int(usage.get("scans_count") or 0)
    questions_used_for_scan: int | None = None
    if scan_id is not None:
        questions_used_for_scan = int((usage.get("questions_per_scan") or {}).get(str(scan_id), 0))

    return LimitsResponse(
        plan=plan,
        month=month,
        scans_used=scans_used,
        scans_max=caps["monthly_scans"],
        scans_remaining=max(caps["monthly_scans"] - scans_used, 0),
        max_pages_per_scan=caps["max_pages_per_scan"],
        questions_used_for_scan=questions_used_for_scan,
        max_questions_per_scan=caps["max_questions_per_scan"],
    )
