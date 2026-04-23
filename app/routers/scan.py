import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import get_settings
from app.middleware.auth import get_current_user_id
from app.models.schemas import (
    DeadlineItem,
    ScanListItem,
    ScanRequest,
    ScanResponse,
    UserLanguage,
)
from app.routers.profile import _ensure_profile
from app.services.explain import analyze_document
from app.services.ocr import classify_document
from app.services.rag import get_supabase, retrieve_chunks

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scans", tags=["scans"])


async def _sign_image_url(image_path: str) -> str:
    """Get a short-lived signed URL so GPT Vision can fetch a private storage object."""
    settings = get_settings()
    supabase = await get_supabase()
    try:
        signed = await supabase.storage.from_(settings.supabase_storage_bucket).create_signed_url(
            image_path, settings.supabase_signed_url_ttl
        )
    except Exception as exc:
        logger.error("Failed to sign URL for %s: %s", image_path, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Could not sign image URL — is the file uploaded to bucket '{settings.supabase_storage_bucket}'?",
        ) from exc

    image_url = signed.get("signedURL") or signed.get("signedUrl")
    if not image_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Storage returned no signed URL"
        )
    return image_url


async def _create_deadlines(user_id: UUID, scan_id: UUID, deadlines: list[DeadlineItem]) -> int:
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
        }
        for d in deadlines
    ]
    result = await supabase.table("deadlines").insert(rows).execute()
    return len(result.data or [])


@router.post("", response_model=ScanResponse, status_code=status.HTTP_201_CREATED)
async def create_scan(
    body: ScanRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> ScanResponse:
    settings = get_settings()
    supabase = await get_supabase()

    profile = await _ensure_profile(user_id)
    language: UserLanguage = body.language or profile.get("language", "cs")
    has_datova_schranka: bool = bool(profile.get("has_datova_schranka", False))

    image_url = await _sign_image_url(body.image_path)

    # Call 1 — OCR + classification
    classifier = await classify_document(image_url)

    # Quality gate — stop early if OCR is unusable, don't burn call 2
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

    # RAG — contextual chunks for this category
    rag_chunks = await retrieve_chunks(classifier.ocr_text, classifier.category)

    # Call 2 — analysis
    analysis, model_used = await analyze_document(
        classifier=classifier,
        rag_chunks=rag_chunks,
        language=language,
        has_datova_schranka=has_datova_schranka,
    )

    # Persist
    scan_row = {
        "user_id": str(user_id),
        "image_path": body.image_path,
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

    deadlines_created = await _create_deadlines(user_id, scan_id, analysis.deadlines)

    logger.info(
        "Scan created scan_id=%s category=%s model=%s ocr_conf=%.2f deadlines=%d",
        scan_id,
        classifier.category,
        model_used,
        classifier.confidence,
        deadlines_created,
    )
    return ScanResponse.model_validate(scan_data)


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
        .select("id,category,doc_type,result,created_at")
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
