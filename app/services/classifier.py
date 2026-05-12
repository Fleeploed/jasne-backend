"""Classifier service — call 1 (GPT-4o Vision, multi-page, detail: high).

Skill: "Vision ВСЕГДА на полном GPT-4o" — see config.openai_classifier_model.
Multi-page: takes a list of signed Storage URLs, sends them all in one call.
"""

import json
import logging

from pydantic import ValidationError

from app.config import get_settings
from app.models.schemas import ClassifierResult
from app.prompts import CLASSIFIER_SYSTEM_PROMPT
from app.services.embeddings import get_openai_client

logger = logging.getLogger(__name__)


def _fallback(reason: str, page_count: int = 1) -> ClassifierResult:
    return ClassifierResult(
        ocr_text="",
        category="ostatni",
        doc_type="unknown",
        sender=None,
        document_language="cs",
        confidence=0.0,
        page_count=page_count,
        error=reason,
    )


async def classify_document(image_urls: list[str]) -> ClassifierResult:
    """Run OCR + classification on one or more pages of the same document.

    All pages are sent in a single Vision call so the model can use
    cross-page context (running header, page numbering, "pokračování na další
    straně", etc).
    """
    if not image_urls:
        # page_count must be >=1 by schema; the `error` field carries the truth.
        return _fallback("No image URLs provided")

    client = get_openai_client()
    settings = get_settings()

    image_parts = [{"type": "image_url", "image_url": {"url": url, "detail": "high"}} for url in image_urls]

    response = await client.chat.completions.create(
        model=settings.openai_classifier_model,
        temperature=0,
        max_tokens=settings.openai_classifier_max_output_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": image_parts},
        ],
    )

    raw = response.choices[0].message.content or "{}"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Classifier returned invalid JSON: %s | %s", exc, raw[:200])
        return _fallback("Invalid JSON from classifier", page_count=len(image_urls))

    # Default page_count from input if model didn't return one (some prompt drift).
    data.setdefault("page_count", len(image_urls))

    try:
        result = ClassifierResult.model_validate(data)
    except ValidationError as exc:
        logger.error("Classifier JSON failed validation: %s | data=%s", exc, data)
        return _fallback(
            f"Schema validation failed: {exc.errors()[0]['msg']}",
            page_count=len(image_urls),
        )

    logger.info(
        "Classified pages=%d category=%s doc_type=%s confidence=%.2f lang=%s",
        result.page_count,
        result.category,
        result.doc_type,
        result.confidence,
        result.document_language,
    )
    return result
