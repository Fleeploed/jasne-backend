from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.middleware.auth import get_current_user_id
from app.models.schemas import OampStatusCheckRequest, OampStatusCheckResponse
from app.services.oamp_status import (
    OampStatusError,
    OampStatusTimeoutError,
    OampStatusUnsupportedError,
    check_oamp_status,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/status", tags=["status"])


@router.post("/oamp", response_model=OampStatusCheckResponse)
async def check_oamp_status_endpoint(
    payload: OampStatusCheckRequest,
    _user_id: UUID = Depends(get_current_user_id),
) -> OampStatusCheckResponse:
    try:
        return await asyncio.to_thread(check_oamp_status, payload)
    except OampStatusUnsupportedError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except OampStatusTimeoutError as exc:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=str(exc)) from exc
    except OampStatusError as exc:
        logger.warning("OAMP status lookup failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not check OAMP status right now",
        ) from exc
