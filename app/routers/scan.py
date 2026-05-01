"""POST /scans — multi-page scan: OCR → RAG → analyzer → persist + open chat thread."""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.middleware.auth import get_current_user_id
from app.models.schemas import (
    AnalysisResult,
    ClassifierResult,
    DeadlineItem,
    ScanListItem,
    ScanRequest,
    ScanResponse,
    UserLanguage,
)
from app.routers.profile import _ensure_profile
from app.services.analyzer import analyze_document, stream_analyze_document
from app.services.classifier import classify_document
from app.services.limits import check_scan_quota, increment_scan_count
from app.services.rag import get_supabase, retrieve_chunks

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scans", tags=["scans"])


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _sign_image_urls(image_paths: list[str]) -> list[str]:
    """Get short-lived signed URLs for every page so GPT Vision can fetch them."""
    settings = get_settings()
    supabase = await get_supabase()
    urls: list[str] = []

    for path in image_paths:
        try:
            signed = await supabase.storage.from_(settings.supabase_storage_bucket).create_signed_url(
                path, settings.supabase_signed_url_ttl
            )
        except Exception as exc:
            logger.error("Failed to sign URL for %s: %s", path, exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Could not sign image URL for '{path}' — is it uploaded "
                    f"to bucket '{settings.supabase_storage_bucket}'?"
                ),
            ) from exc

        url = signed.get("signedURL") or signed.get("signedUrl")
        if not url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Storage returned no signed URL",
            )
        urls.append(url)

    return urls


async def _create_deadlines(user_id: UUID, scan_id: UUID, deadlines: list[DeadlineItem]) -> int:
    """Persist analyzer-suggested deadlines as auto_created=true."""
    if not deadlines:
        return 0
    supabase = await get_supabase()
    rows = [
        {
            "user_id": str(user_id),
            "scan_id": str(scan_id),
            "title": d.description[:200],
            "description": d.description,
            "due_date": d.date.isoformat(),
            "deadline_type": d.type,
            "auto_created": True,
        }
        for d in deadlines
    ]
    result = await supabase.table("deadlines").insert(rows).execute()
    return len(result.data or [])


async def _seed_chat_thread(user_id: UUID, scan_id: UUID, image_paths: list[str], summary: str) -> None:
    """Open the chat thread for this scan with two seed messages:
    user uploads photo(s), AI replies with the result card."""
    supabase = await get_supabase()
    rows = [
        {
            "scan_id": str(scan_id),
            "user_id": str(user_id),
            "role": "user",
            "content": "\n".join(image_paths),
            "message_type": "image",
        },
        {
            "scan_id": str(scan_id),
            "user_id": str(user_id),
            "role": "assistant",
            "content": summary,
            "message_type": "result_card",
        },
    ]
    await supabase.table("scan_messages").insert(rows).execute()


async def _persist_completed_scan(
    user_id: UUID,
    body: ScanRequest,
    classifier: ClassifierResult,
    analysis: AnalysisResult,
    language: UserLanguage,
    model_used: str,
) -> ScanResponse:
    supabase = await get_supabase()
    scan_row = {
        "user_id": str(user_id),
        "image_paths": body.image_paths,
        "ocr_text": classifier.ocr_text,
        "category": classifier.category,
        "doc_type": classifier.doc_type,
        "category_confidence": classifier.confidence,
        "result": analysis.model_dump(mode="json"),
        "language": language,
        "model_used": model_used,
    }
    inserted = await supabase.table("scans").insert(scan_row).execute()
    if not inserted.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to save scan")

    scan_data = inserted.data[0]
    scan_id = UUID(scan_data["id"])

    await increment_scan_count(user_id)
    deadlines_created = await _create_deadlines(user_id, scan_id, analysis.deadlines)
    await _seed_chat_thread(user_id, scan_id, body.image_paths, analysis.summary)

    logger.info(
        "Scan created scan_id=%s pages=%d category=%s model=%s ocr_conf=%.2f deadlines=%d",
        scan_id,
        scan_data.get("page_count") or len(body.image_paths),
        classifier.category,
        model_used,
        classifier.confidence,
        deadlines_created,
    )
    return ScanResponse.model_validate(scan_data)


@router.post("", response_model=ScanResponse, status_code=status.HTTP_201_CREATED)
async def create_scan(
    body: ScanRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> ScanResponse:
    settings = get_settings()

    profile = await _ensure_profile(user_id)
    plan = profile.get("plan", "free")
    language: UserLanguage = body.language or profile.get("language", "cs")
    has_datova_schranka: bool = bool(profile.get("has_datova_schranka", False))

    # Plan-based quota check (monthly scans + per-scan page cap)
    try:
        await check_scan_quota(user_id, plan, page_count=len(body.image_paths))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)) from exc

    image_urls = await _sign_image_urls(body.image_paths)

    # Call 1 — OCR + classification (multi-page, single Vision call)
    classifier = await classify_document(image_urls)

    if classifier.confidence < settings.min_ocr_confidence or not classifier.ocr_text.strip():
        logger.warning(
            "OCR quality gate failed user_id=%s confidence=%.2f error=%s",
            user_id,
            classifier.confidence,
            classifier.error,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not read document — try a better photo. {classifier.error or ''}".strip(),
        )

    # RAG — pull category-specific knowledge chunks
    rag_chunks = await retrieve_chunks(classifier.ocr_text, classifier.category)

    # Call 2 — analyzer (default model or escalation, picked by category)
    analysis, model_used = await analyze_document(
        classifier=classifier,
        rag_chunks=rag_chunks,
        language=language,
        has_datova_schranka=has_datova_schranka,
    )

    return await _persist_completed_scan(user_id, body, classifier, analysis, language, model_used)


@router.post("/stream")
async def create_scan_stream(
    body: ScanRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> StreamingResponse:
    settings = get_settings()
    profile = await _ensure_profile(user_id)
    plan = profile.get("plan", "free")
    language: UserLanguage = body.language or profile.get("language", "cs")
    has_datova_schranka: bool = bool(profile.get("has_datova_schranka", False))

    try:
        await check_scan_quota(user_id, plan, page_count=len(body.image_paths))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)) from exc

    async def events() -> AsyncIterator[str]:
        try:
            yield _sse("status", {"step": "signing_urls", "message": "Preparing document images"})
            image_urls = await _sign_image_urls(body.image_paths)

            yield _sse("status", {"step": "ocr", "message": "Reading document text"})
            classifier = await classify_document(image_urls)
            yield _sse(
                "status",
                {
                    "step": "classified",
                    "category": classifier.category,
                    "doc_type": classifier.doc_type,
                    "confidence": classifier.confidence,
                },
            )

            if classifier.confidence < settings.min_ocr_confidence or not classifier.ocr_text.strip():
                detail = f"Could not read document — try a better photo. {classifier.error or ''}".strip()
                logger.warning(
                    "OCR quality gate failed user_id=%s confidence=%.2f error=%s",
                    user_id,
                    classifier.confidence,
                    classifier.error,
                )
                yield _sse("error", {"status_code": status.HTTP_422_UNPROCESSABLE_ENTITY, "detail": detail})
                return

            yield _sse("status", {"step": "rag", "message": "Checking relevant Czech rules"})
            rag_chunks = await retrieve_chunks(classifier.ocr_text, classifier.category)

            yield _sse("status", {"step": "analysis", "message": "Finding summary, actions, and risks"})
            analysis: AnalysisResult | None = None
            model_used: str | None = None
            async for item in stream_analyze_document(
                classifier=classifier,
                rag_chunks=rag_chunks,
                language=language,
                has_datova_schranka=has_datova_schranka,
            ):
                if item["type"] == "field":
                    yield _sse("field", {"field": item["field"], "value": item["value"]})
                elif item["type"] == "done":
                    analysis = item["analysis"]
                    model_used = item["model_used"]

            if analysis is None or model_used is None:
                raise RuntimeError("Analyzer stream ended without a final result")

            yield _sse("status", {"step": "saving", "message": "Saving scan and reminders"})
            scan = await _persist_completed_scan(user_id, body, classifier, analysis, language, model_used)
            yield _sse("done", scan.model_dump(mode="json"))
        except Exception as exc:
            logger.exception("Streaming scan failed user_id=%s", user_id)
            yield _sse("error", {"detail": str(exc) or "Streaming scan failed"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("", response_model=list[ScanListItem])
async def list_scans(
    limit: int = 20,
    offset: int = 0,
    user_id: UUID = Depends(get_current_user_id),
) -> list[ScanListItem]:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    supabase = await get_supabase()
    result = (
        await supabase.table("scans")
        .select("id,category,doc_type,result,page_count,image_paths,created_at")
        .eq("user_id", str(user_id))
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return [ScanListItem.from_row(row) for row in (result.data or [])]


@router.get("/{scan_id}", response_model=ScanResponse)
async def get_scan(
    scan_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
) -> ScanResponse:
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
    return ScanResponse.model_validate(result.data[0])


@router.delete("/{scan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scan(
    scan_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
) -> None:
    supabase = await get_supabase()
    await supabase.table("scans").delete().eq("id", str(scan_id)).eq("user_id", str(user_id)).execute()
    logger.info("Scan deleted scan_id=%s user_id=%s", scan_id, user_id)
