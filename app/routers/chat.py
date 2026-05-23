"""GET /scans/{scan_id}/chat — list messages.
POST /scans/{scan_id}/chat — ask a follow-up question (Pro only, capped 10/scan).
"""

import json
import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.middleware.auth import get_current_user_id
from app.models.schemas import ChatAskRequest, ChatAskResponse, ScanMessage, UserLanguage
from app.routers.profile import _ensure_profile
from app.services.chat import answer_question, fetch_scan_history, stream_answer_chunks
from app.services.limits import (
    caps_for_plan,
    check_chat_quota,
    increment_chat_count,
    usage_period_for_profile,
)
from app.services.rag import get_supabase, retrieve_chat_chunks

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


async def _persist_chat_pair(
    scan_id: UUID,
    user_id: UUID,
    question: str,
    answer: str,
) -> tuple[ScanMessage, ScanMessage]:
    supabase = await get_supabase()
    inserted = await (
        supabase.table("scan_messages")
        .insert(
            [
                {
                    "scan_id": str(scan_id),
                    "user_id": str(user_id),
                    "role": "user",
                    "content": question,
                    "message_type": "text",
                },
                {
                    "scan_id": str(scan_id),
                    "user_id": str(user_id),
                    "role": "assistant",
                    "content": answer,
                    "message_type": "text",
                },
            ]
        )
        .execute()
    )

    if not inserted.data or len(inserted.data) != 2:
        logger.error("Failed to persist chat messages scan_id=%s user_id=%s", scan_id, user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist chat messages",
        )

    return ScanMessage.model_validate(inserted.data[0]), ScanMessage.model_validate(inserted.data[1])


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _build_chat_rag_query(question: str, scan_row: dict) -> str:
    """Add stable scan context to short follow-up questions.

    Questions like "часы работы" have no address tokens, so lexical RAG cannot
    find the office-hours chunk unless we include the scan's known destination.
    """
    result = scan_row.get("result") or {}
    context_parts = [
        question,
        str(result.get("where_to_go") or ""),
        str(result.get("office_hours") or ""),
    ]
    return "\n".join(part for part in context_parts if part.strip())


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
    usage_period = usage_period_for_profile(profile)
    language: UserLanguage = profile.get("language", "cs")

    # Quota first — cheaper to fail before loading scan + history.
    try:
        await check_chat_quota(user_id, plan, scan_id, month=usage_period)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)) from exc

    scan_row = await _load_scan(scan_id, user_id)
    language: UserLanguage = scan_row.get("language") or language
    history = await fetch_scan_history(scan_id, user_id)
    rag_chunks = await retrieve_chat_chunks(
        _build_chat_rag_query(body.question, scan_row),
        scan_row.get("category"),
        language,
    )

    # Run LLM
    answer = await answer_question(
        scan_row=scan_row,
        history=history,
        question=body.question,
        language=language,
        rag_chunks=rag_chunks,
    )

    user_message, assistant_message = await _persist_chat_pair(scan_id, user_id, body.question, answer)

    new_count = await increment_chat_count(user_id, scan_id, month=usage_period)
    caps = caps_for_plan(plan)

    return ChatAskResponse(
        user_message=user_message,
        assistant_message=assistant_message,
        questions_used=new_count,
        questions_remaining=max(caps["max_questions_per_scan"] - new_count, 0),
    )


@router.post("/stream")
async def ask_question_stream(
    scan_id: UUID,
    body: ChatAskRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> StreamingResponse:
    profile = await _ensure_profile(user_id)
    plan = profile.get("plan", "free")
    usage_period = usage_period_for_profile(profile)
    language: UserLanguage = profile.get("language", "cs")

    try:
        await check_chat_quota(user_id, plan, scan_id, month=usage_period)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)) from exc

    scan_row = await _load_scan(scan_id, user_id)
    language: UserLanguage = scan_row.get("language") or language
    history = await fetch_scan_history(scan_id, user_id)
    rag_chunks = await retrieve_chat_chunks(
        _build_chat_rag_query(body.question, scan_row),
        scan_row.get("category"),
        language,
    )
    caps = caps_for_plan(plan)

    async def events() -> AsyncIterator[str]:
        answer_parts: list[str] = []
        try:
            async for chunk in stream_answer_chunks(
                scan_row=scan_row,
                history=history,
                question=body.question,
                language=language,
                rag_chunks=rag_chunks,
            ):
                answer_parts.append(chunk)
                yield _sse("token", {"text": chunk})

            answer = "".join(answer_parts).strip()
            if not answer:
                answer = "Bohužel jsem nemohl odpovědět. Zkuste prosím přeformulovat otázku."
                yield _sse("token", {"text": answer})

            user_message, assistant_message = await _persist_chat_pair(
                scan_id, user_id, body.question, answer
            )
            new_count = await increment_chat_count(user_id, scan_id, month=usage_period)
            yield _sse(
                "done",
                {
                    "user_message": user_message.model_dump(mode="json"),
                    "assistant_message": assistant_message.model_dump(mode="json"),
                    "questions_used": new_count,
                    "questions_remaining": max(caps["max_questions_per_scan"] - new_count, 0),
                },
            )
        except Exception as exc:
            logger.exception("Streaming chat failed scan_id=%s user_id=%s", scan_id, user_id)
            yield _sse("error", {"detail": str(exc) or "Streaming chat failed"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
