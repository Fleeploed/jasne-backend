"""GET /scans/{scan_id}/chat — list messages.
POST /scans/{scan_id}/chat — ask a follow-up question (Pro only, capped 10/scan).
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.middleware.auth import get_current_user_id
from app.models.schemas import ChatAskRequest, ChatAskResponse, ScanMessage, UserLanguage
from app.routers.profile import _ensure_profile
from app.services.chat import answer_question, fetch_scan_history
from app.services.limits import (
    caps_for_plan,
    check_chat_quota,
    increment_chat_count,
)
from app.services.rag import get_supabase

logger = logging.getLogger(__name__)

# Mounted under /scans/{scan_id}/chat to keep the URL hierarchical.
router = APIRouter(prefix="/scans/{scan_id}/chat", tags=["chat"])


async def _load_scan(scan_id: UUID, user_id: UUID) -> dict:
    supabase = await get_supabase()
    result = (
        await supabase.table("scans")
        .select("*")
        .eq("id", str(scan_id))
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found")
    return result.data[0]


@router.get("", response_model=list[ScanMessage])
async def list_messages(
    scan_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
) -> list[ScanMessage]:
    # Ownership check via the scan first — also gives a clean 404.
    await _load_scan(scan_id, user_id)

    supabase = await get_supabase()
    result = (
        await supabase.table("scan_messages")
        .select("*")
        .eq("scan_id", str(scan_id))
        .eq("user_id", str(user_id))
        .order("created_at")
        .execute()
    )
    return [ScanMessage.model_validate(row) for row in (result.data or [])]


@router.post("", response_model=ChatAskResponse, status_code=status.HTTP_201_CREATED)
async def ask_question(
    scan_id: UUID,
    body: ChatAskRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> ChatAskResponse:
    profile = await _ensure_profile(user_id)
    plan = profile.get("plan", "free")
    language: UserLanguage = profile.get("language", "cs")

    # Quota first — cheaper to fail before loading scan + history.
    try:
        await check_chat_quota(user_id, plan, scan_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)) from exc

    scan_row = await _load_scan(scan_id, user_id)
    history = await fetch_scan_history(scan_id, user_id)

    # Run LLM
    answer = await answer_question(
        scan_row=scan_row,
        history=history,
        question=body.question,
        language=language,
    )

    # Persist both messages atomically (best-effort — Supabase doesn't expose
    # client-side transactions; if the second insert fails we'll log but the
    # user still saw the answer in their response).
    supabase = await get_supabase()
    insert_user = await (
        supabase.table("scan_messages")
        .insert(
            {
                "scan_id": str(scan_id),
                "user_id": str(user_id),
                "role": "user",
                "content": body.question,
                "message_type": "text",
            }
        )
        .execute()
    )
    insert_ai = await (
        supabase.table("scan_messages")
        .insert(
            {
                "scan_id": str(scan_id),
                "user_id": str(user_id),
                "role": "assistant",
                "content": answer,
                "message_type": "text",
            }
        )
        .execute()
    )

    if not insert_user.data or not insert_ai.data:
        # Don't 500 — the user got their answer. Just log.
        logger.error("Failed to persist chat messages scan_id=%s user_id=%s", scan_id, user_id)

    new_count = await increment_chat_count(user_id, scan_id)
    caps = caps_for_plan(plan)

    return ChatAskResponse(
        user_message=ScanMessage.model_validate(insert_user.data[0]),
        assistant_message=ScanMessage.model_validate(insert_ai.data[0]),
        questions_used=new_count,
        questions_remaining=max(caps["max_questions_per_scan"] - new_count, 0),
    )
