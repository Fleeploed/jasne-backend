"""POST /scans — multi-page scan: OCR → RAG → analyzer → persist + open chat thread."""

import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.middleware.auth import get_current_user_id
from app.models.schemas import (
    AnalysisResult,
    ClassifierResult,
    DeadlineItem,
    ScanImageUrlsResponse,
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

SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
PDF_EXTENSION = ".pdf"
PDF_RENDER_SCALE = 2.0
MAX_PDF_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class PreparedVisionInputs:
    """Prepared GPT Vision inputs after storage validation and PDF rendering."""

    urls: list[str]
    page_count: int


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# iOS NSURLSession-backed XHR drops the connection after ~60s of silence.
# Heartbeats are SSE comment lines (ignored by parsers) keeping the socket alive
# during long blocking calls (10-page GPT-4o Vision can take 30-60+ seconds).
SSE_HEARTBEAT_INTERVAL = 10.0
SSE_HEARTBEAT = ":keep-alive\n\n"


async def _yield_heartbeats_until_complete(
    task: asyncio.Task[Any], interval: float = SSE_HEARTBEAT_INTERVAL
) -> AsyncIterator[str]:
    """Yield SSE heartbeat lines until `task` finishes. Task exceptions are not re-raised here —
    the caller must inspect `task.result()` after this iterator exits."""
    while not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=interval)
        except TimeoutError:
            yield SSE_HEARTBEAT
        except BaseException:
            # Task failed — let caller handle via task.exception()/result().
            return


async def _stream_analyzer_phase(
    classifier: ClassifierResult,
    rag_chunks: list[str],
    language: UserLanguage,
    has_datova_schranka: bool,
) -> AsyncIterator[Any]:
    """Stream analyzer fields with heartbeats. Yields SSE strings; final yield is the
    (analysis, model_used) tuple. Raises RuntimeError if the stream ends without a result."""
    analyzer_iter = stream_analyze_document(
        classifier=classifier,
        rag_chunks=rag_chunks,
        language=language,
        has_datova_schranka=has_datova_schranka,
    ).__aiter__()
    analysis: AnalysisResult | None = None
    model_used: str | None = None
    while True:
        next_task = asyncio.create_task(analyzer_iter.__anext__())
        async for heartbeat in _yield_heartbeats_until_complete(next_task):
            yield heartbeat
        try:
            item = next_task.result()
        except StopAsyncIteration:
            break
        if item["type"] == "field":
            yield _sse("field", {"field": item["field"], "value": item["value"]})
        elif item["type"] == "done":
            analysis = item["analysis"]
            model_used = item["model_used"]
    if analysis is None or model_used is None:
        raise RuntimeError("Analyzer stream ended without a final result")
    yield (analysis, model_used)


async def _scan_stream_events(
    user_id: UUID,
    body: ScanRequest,
    plan: str,
    language: UserLanguage,
    has_datova_schranka: bool,
) -> AsyncIterator[str]:
    """Compose the full SSE event sequence for a single scan request."""
    try:
        yield _sse("status", {"step": "preparing_inputs", "message": "Preparing document pages"})
        vision_inputs = await _prepare_vision_inputs(user_id, body.image_paths)
        await _check_scan_quota_or_402(user_id, plan, page_count=vision_inputs.page_count)

        yield _sse("status", {"step": "ocr", "message": "Reading document text"})
        classifier_task = asyncio.create_task(classify_document(vision_inputs.urls))
        async for heartbeat in _yield_heartbeats_until_complete(classifier_task):
            yield heartbeat
        classifier = classifier_task.result().model_copy(update={"page_count": vision_inputs.page_count})
        yield _sse(
            "status",
            {
                "step": "classified",
                "category": classifier.category,
                "doc_type": classifier.doc_type,
                "confidence": classifier.confidence,
            },
        )

        if not classifier.ocr_text.strip():
            detail = f"Could not read document — try a better photo. {classifier.error or ''}".strip()
            logger.warning(
                "OCR quality gate failed (empty text) user_id=%s confidence=%.2f error=%s",
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
        async for item in _stream_analyzer_phase(classifier, rag_chunks, language, has_datova_schranka):
            if isinstance(item, str):
                yield item
            else:
                analysis, model_used = item

        assert analysis is not None and model_used is not None  # noqa: S101

        yield _sse("status", {"step": "saving", "message": "Saving scan and reminders"})
        scan = await _persist_completed_scan(user_id, body, classifier, analysis, language, model_used)
        yield _sse("done", scan.model_dump(mode="json"))
    except HTTPException as exc:
        yield _sse("error", {"status_code": exc.status_code, "detail": exc.detail})
    except Exception as exc:
        logger.exception("Streaming scan failed user_id=%s", user_id)
        yield _sse("error", {"detail": str(exc) or "Streaming scan failed"})


def _storage_suffix(path: str) -> str:
    return PurePosixPath(path.split("?", 1)[0]).suffix.lower()


def _validate_storage_path(user_id: UUID, path: str) -> str:
    """Ensure service-role signing is limited to files under the current user prefix."""
    clean_path = path.strip()
    user_prefix = f"{user_id}/"
    parts = PurePosixPath(clean_path).parts

    if not clean_path:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Storage path is blank")
    if clean_path.startswith("/") or "://" in clean_path or "\x00" in clean_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid storage path: '{path}'")
    if ".." in parts:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid storage path: '{path}'")
    if not clean_path.startswith(user_prefix):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Storage path must belong to the authenticated user",
        )
    return clean_path


async def _sign_storage_url(path: str) -> str:
    """Get a short-lived signed URL for a storage object."""
    settings = get_settings()
    supabase = await get_supabase()

    try:
        signed = await supabase.storage.from_(settings.supabase_storage_bucket).create_signed_url(
            path, settings.supabase_signed_url_ttl
        )
    except Exception as exc:
        logger.error("Failed to sign URL for %s: %s", path, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Could not sign storage URL for '{path}' — is it uploaded "
                f"to bucket '{settings.supabase_storage_bucket}'?"
            ),
        ) from exc

    url = signed.get("signedURL") or signed.get("signedUrl")
    if not url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Storage returned no signed URL",
        )
    return url


async def _download_signed_object(url: str, path: str) -> bytes:
    """Download a signed Supabase object that must be transformed before GPT Vision."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        logger.error("Failed to download signed storage object %s: %s", path, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not download PDF '{path}' from storage",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not download PDF '{path}' from storage",
        )

    content = response.content
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="PDF is too large. Upload up to 20 MB or split it into images.",
        )
    return content


def _render_pdf_to_png_data_urls(pdf_bytes: bytes, path: str) -> list[str]:
    """Render a PDF into PNG data URLs accepted by OpenAI Vision."""
    try:
        import fitz
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PDF support is not installed on the server",
        ) from exc

    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid PDF: '{path}'"
        ) from exc

    try:
        page_count = int(document.page_count)
        if page_count < 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"PDF has no pages: '{path}'"
            )
        if page_count > 10:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="PDF has more than 10 pages. Split it into a smaller document.",
            )

        try:
            matrix = fitz.Matrix(PDF_RENDER_SCALE, PDF_RENDER_SCALE)
            data_urls: list[str] = []
            for page_index in range(page_count):
                page = document.load_page(page_index)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                png_bytes = pixmap.tobytes("png")
                encoded = base64.b64encode(png_bytes).decode("ascii")
                data_urls.append(f"data:image/png;base64,{encoded}")
            return data_urls
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Could not render PDF: '{path}'",
            ) from exc
    finally:
        document.close()


async def _prepare_vision_inputs(user_id: UUID, image_paths: list[str]) -> PreparedVisionInputs:
    """Validate storage paths and convert supported inputs into GPT Vision URLs."""
    urls: list[str] = []

    for raw_path in image_paths:
        path = _validate_storage_path(user_id, raw_path)
        suffix = _storage_suffix(path)

        if suffix in SUPPORTED_IMAGE_EXTENSIONS:
            urls.append(await _sign_storage_url(path))
            continue

        if suffix == PDF_EXTENSION:
            signed_url = await _sign_storage_url(path)
            pdf_bytes = await _download_signed_object(signed_url, path)
            urls.extend(_render_pdf_to_png_data_urls(pdf_bytes, path))
            continue

        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file type. Upload PNG, JPEG, GIF, WEBP, or PDF.",
        )

    return PreparedVisionInputs(urls=urls, page_count=len(urls))


async def _check_scan_quota_or_402(user_id: UUID, plan: str, page_count: int) -> None:
    try:
        await check_scan_quota(user_id, plan, page_count=page_count)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)) from exc


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
        "page_count": classifier.page_count,
    }
    try:
        inserted = await supabase.table("scans").insert(scan_row).execute()
    except Exception as exc:
        message = str(exc).lower()
        if "page_count" not in message or "generated" not in message:
            raise

        logger.warning(
            "scans.page_count is still generated in the database; "
            "retrying insert without explicit page_count. Apply migration 0005."
        )
        legacy_scan_row = dict(scan_row)
        legacy_scan_row.pop("page_count", None)
        inserted = await supabase.table("scans").insert(legacy_scan_row).execute()

    if not inserted.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to save scan")

    scan_data = inserted.data[0]
    scan_data["page_count"] = classifier.page_count
    scan_id = UUID(scan_data["id"])

    await increment_scan_count(user_id)

    # Side-effects below must not fail the scan — the row is already committed.
    # We log and continue so the user gets their analysis back even if reminders
    # or the chat seed transiently fail.
    try:
        deadlines_created = await _create_deadlines(user_id, scan_id, analysis.deadlines)
    except Exception:
        logger.exception("Failed to create deadlines scan_id=%s", scan_id)
        deadlines_created = 0

    try:
        await _seed_chat_thread(user_id, scan_id, body.image_paths, analysis.summary)
    except Exception:
        logger.exception("Failed to seed chat thread scan_id=%s", scan_id)

    logger.info(
        "Scan created scan_id=%s pages=%d category=%s model=%s ocr_conf=%.2f deadlines=%d",
        scan_id,
        classifier.page_count,
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
    profile = await _ensure_profile(user_id)
    plan = profile.get("plan", "free")
    language: UserLanguage = body.language or profile.get("language", "cs")
    has_datova_schranka: bool = bool(profile.get("has_datova_schranka", False))

    # Plan-based quota check (monthly scans + per-scan page cap)
    await _check_scan_quota_or_402(user_id, plan, page_count=len(body.image_paths))

    vision_inputs = await _prepare_vision_inputs(user_id, body.image_paths)
    await _check_scan_quota_or_402(user_id, plan, page_count=vision_inputs.page_count)

    # Call 1 — OCR + classification (multi-page, single Vision call)
    classifier = await classify_document(vision_inputs.urls)
    classifier = classifier.model_copy(update={"page_count": vision_inputs.page_count})

    if not classifier.ocr_text.strip():
        logger.warning(
            "OCR quality gate failed (empty text) user_id=%s confidence=%.2f error=%s",
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
    profile = await _ensure_profile(user_id)
    plan = profile.get("plan", "free")
    language: UserLanguage = body.language or profile.get("language", "cs")
    has_datova_schranka: bool = bool(profile.get("has_datova_schranka", False))

    await _check_scan_quota_or_402(user_id, plan, page_count=len(body.image_paths))

    return StreamingResponse(
        _scan_stream_events(user_id, body, plan, language, has_datova_schranka),
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
    result = await supabase.rpc(
        "list_scans_with_activity",
        {"p_user_id": str(user_id), "p_limit": limit, "p_offset": offset},
    ).execute()
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


@router.get("/{scan_id}/image-urls", response_model=ScanImageUrlsResponse)
async def get_scan_image_urls(
    scan_id: UUID,
    render_pdf: bool = False,
    user_id: UUID = Depends(get_current_user_id),
) -> ScanImageUrlsResponse:
    """Return signed preview URLs for files attached to a scan owned by the user."""
    supabase = await get_supabase()
    result = (
        await supabase.table("scans")
        .select("image_paths")
        .eq("id", str(scan_id))
        .eq("user_id", str(user_id))
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found")

    urls = []
    for raw_path in result.data[0].get("image_paths") or []:
        path = _validate_storage_path(user_id, raw_path)
        suffix = _storage_suffix(path)
        if suffix in SUPPORTED_IMAGE_EXTENSIONS:
            urls.append(await _sign_storage_url(path))
        elif suffix == PDF_EXTENSION:
            signed_url = await _sign_storage_url(path)
            if render_pdf:
                pdf_bytes = await _download_signed_object(signed_url, path)
                urls.extend(_render_pdf_to_png_data_urls(pdf_bytes, path))
            else:
                urls.append(signed_url)

    return ScanImageUrlsResponse(urls=urls)


@router.delete("/{scan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scan(
    scan_id: UUID,
    user_id: UUID = Depends(get_current_user_id),
) -> None:
    supabase = await get_supabase()
    await supabase.table("scans").delete().eq("id", str(scan_id)).eq("user_id", str(user_id)).execute()
    logger.info("Scan deleted scan_id=%s user_id=%s", scan_id, user_id)
