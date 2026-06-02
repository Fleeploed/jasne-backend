"""OCR service — Azure Document Intelligence (prebuilt-read).

Replaces GPT-4o Vision for text extraction. All pages are sent to Azure DI
sequentially, and the results are concatenated with a page separator that the
downstream classifier and analyzer prompts can recognise.

Errors never raise out — empty/failed runs return an OcrResult with an empty
`ocr_text` so the classifier can apply the existing OCR quality gate.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.models.schemas import MAX_PAGES_HARD_LIMIT, OcrResult

logger = logging.getLogger(__name__)

# Page separator between concatenated OCR pages. The downstream classifier
# and analyzer prompts are tolerant of any whitespace, but a stable marker
# helps when debugging or when humans inspect saved scans.ocr_text.
_PAGE_SEPARATOR = "\n\n---\n\n"


def _detect_language(result: object) -> str:
    """Pick the dominant language code from Azure DI's per-page languages.

    Azure DI returns a `languages` list where each item carries a `locale`
    (e.g. "cs", "en-US") and a confidence + span coverage. We fall back to
    "cs" because the product is Czech-first and most documents are Czech
    even when the SDK returns nothing.
    """
    languages = getattr(result, "languages", None) or []
    best: tuple[float, str] | None = None
    for lang in languages:
        locale = getattr(lang, "locale", None) or (lang.get("locale") if isinstance(lang, dict) else None)
        confidence = getattr(lang, "confidence", None)
        if confidence is None and isinstance(lang, dict):
            confidence = lang.get("confidence")
        if not locale:
            continue
        score = float(confidence or 0.0)
        # Normalise "cs-CZ" → "cs" so callers see ISO short codes.
        short = locale.split("-", 1)[0].lower()
        if best is None or score > best[0]:
            best = (score, short)
    return best[1] if best else "cs"


def _page_count(result: object, fallback: int) -> int:
    pages = getattr(result, "pages", None)
    if pages is None and isinstance(result, dict):
        pages = result.get("pages")
    if pages:
        return len(pages)
    return fallback


def _result_text(result: object) -> str:
    """Pull the full text out of an AnalyzeResult.

    `prebuilt-read` populates the top-level `content` with the entire
    document text. We strip trailing whitespace so multi-page concatenation
    stays tidy.
    """
    content = getattr(result, "content", None)
    if content is None and isinstance(result, dict):
        content = result.get("content")
    return (content or "").strip()


async def _analyze_single_url(client_kwargs: dict, url: str) -> object | None:
    """Run prebuilt-read on a single http(s) URL."""
    # Imported lazily so test environments without the SDK installed can still
    # import this module (e.g. when only OcrResult tests are exercised).
    from azure.ai.documentintelligence.aio import DocumentIntelligenceClient  # type: ignore
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest  # type: ignore
    from azure.core.credentials import AzureKeyCredential  # type: ignore

    credential = AzureKeyCredential(client_kwargs["key"])
    async with DocumentIntelligenceClient(
        endpoint=client_kwargs["endpoint"],
        credential=credential,
    ) as client:
        poller = await client.begin_analyze_document(
            "prebuilt-read",
            AnalyzeDocumentRequest(url_source=url),
        )
        return await poller.result()


async def _analyze_data_url(client_kwargs: dict, data_url: str) -> object | None:
    """Run prebuilt-read on a base64 `data:` URI by sending raw bytes."""
    import base64

    from azure.ai.documentintelligence.aio import DocumentIntelligenceClient  # type: ignore
    from azure.ai.documentintelligence.models import AnalyzeDocumentRequest  # type: ignore
    from azure.core.credentials import AzureKeyCredential  # type: ignore

    # data:image/png;base64,XXXX
    try:
        header, payload = data_url.split(",", 1)
    except ValueError as exc:
        raise ValueError("Invalid data URL passed to OCR") from exc
    if ";base64" not in header:
        raise ValueError("Only base64-encoded data URLs are supported")
    raw_bytes = base64.b64decode(payload)

    credential = AzureKeyCredential(client_kwargs["key"])
    async with DocumentIntelligenceClient(
        endpoint=client_kwargs["endpoint"],
        credential=credential,
    ) as client:
        poller = await client.begin_analyze_document(
            "prebuilt-read",
            AnalyzeDocumentRequest(bytes_source=raw_bytes),
        )
        return await poller.result()


async def _safe_analyze(client_kwargs: dict, url: str, index: int) -> tuple[object | None, Exception | None]:
    """Run Azure DI on one URL (http or data URI), capturing any exception."""
    is_data_url = url.startswith("data:")
    try:
        if is_data_url:
            return await _analyze_data_url(client_kwargs, url), None
        return await _analyze_single_url(client_kwargs, url), None
    except httpx.HTTPError as exc:
        logger.warning(
            "Azure DI HTTP error on page %d (%s): %s",
            index + 1,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return None, exc
    except Exception as exc:
        # Azure SDK raises a variety of exceptions (HttpResponseError,
        # ServiceRequestError, ...). Log with traceback so Render logs surface
        # the real cause.
        logger.warning(
            "Azure DI failed on page %d (%s): %s",
            index + 1,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return None, exc


async def extract_text(image_urls: list[str]) -> OcrResult:
    """Run Azure Document Intelligence prebuilt-read against a list of signed URLs.

    Each URL is analysed in its own DI call (prebuilt-read accepts a single
    `url_source` per request, so multi-page documents that arrive as
    multiple image URLs must be fanned out). Returned pages are concatenated
    in the input order using a stable separator.

    Failure modes — never raise:
      * empty `image_urls`            → empty OcrResult, page_count=1
      * Azure errors or empty text    → empty OcrResult, page_count=len(image_urls)
    """
    if not image_urls:
        logger.warning("extract_text called with no image URLs")
        return OcrResult(ocr_text="", page_count=1, document_language="cs")

    settings = get_settings()
    client_kwargs = {
        "endpoint": settings.azure_document_intelligence_endpoint,
        "key": settings.azure_document_intelligence_key,
    }

    page_texts: list[str] = []
    detected_languages: list[str] = []
    inferred_page_count = 0
    first_error: str | None = None

    def _capture(exc: Exception) -> None:
        nonlocal first_error
        if first_error is None:
            first_error = f"{type(exc).__name__}: {exc}"[:300]

    for index, url in enumerate(image_urls):
        result, error = await _safe_analyze(client_kwargs, url, index)
        if error is not None:
            _capture(error)
            continue
        if result is None:
            continue

        text = _result_text(result)
        if text:
            page_texts.append(text)
        else:
            logger.warning("Azure DI returned empty text on page %d", index + 1)

        inferred_page_count += _page_count(result, fallback=1)
        detected_languages.append(_detect_language(result))

    if not page_texts:
        logger.warning(
            "Azure DI extracted no text from %d input(s); returning empty OcrResult",
            len(image_urls),
        )
        return OcrResult(
            ocr_text="",
            page_count=max(1, min(len(image_urls), MAX_PAGES_HARD_LIMIT)),
            document_language="cs",
            error=first_error or "Azure DI returned no text",
        )

    # If any page reported a language, prefer the first non-Czech detection so
    # foreign-language documents don't get hidden behind a "cs" default. Most
    # multi-page docs are homogenous so this is a cheap heuristic.
    language = next((lang for lang in detected_languages if lang and lang != "cs"), None) or "cs"

    page_count = inferred_page_count or len(image_urls)
    logger.info(
        "Azure DI extracted text from %d page(s), language=%s, chars=%d",
        page_count,
        language,
        sum(len(t) for t in page_texts),
    )
    # Clamp to the schema-level page hard cap (MAX_PAGES_HARD_LIMIT = 10).
    return OcrResult(
        ocr_text=_PAGE_SEPARATOR.join(page_texts),
        page_count=max(1, min(page_count, MAX_PAGES_HARD_LIMIT)),
        document_language=language,
    )
