"""Classifier service — call 1 (GPT-4o text-only).

Accepts OCR text already extracted by Azure Document Intelligence and returns
a category, doc_type, sender, and confidence. No images are ever sent to
OpenAI here — vision has moved out entirely.
"""

import json
import logging

from pydantic import ValidationError

from app.config import get_settings
from app.models.schemas import ClassifierResult
from app.prompts import CLASSIFIER_SYSTEM_PROMPT
from app.services.embeddings import get_openai_client

logger = logging.getLogger(__name__)

# A document with fewer than this many words almost certainly didn't OCR
# correctly. Match the prompt's "too short" rule so we don't even call the LLM.
_MIN_WORDS_FOR_CLASSIFICATION = 20


def _fallback(reason: str) -> ClassifierResult:
    return ClassifierResult(
        category="ostatni",
        doc_type="unknown",
        sender=None,
        confidence=0.0,
        error=reason,
    )


async def classify_text(
    ocr_text: str,
    page_count: int,
    document_language: str,
) -> ClassifierResult:
    """Classify already-extracted OCR text into one of the 10 categories.

    Returns a fallback `ostatni` result with confidence 0.0 and an `error`
    field set when the OCR is empty, too short, or when GPT returns invalid
    JSON. Never raises.
    """
    text = (ocr_text or "").strip()
    if not text:
        return _fallback("OCR text is empty")

    if len(text.split()) < _MIN_WORDS_FOR_CLASSIFICATION:
        # The prompt also enforces this, but skipping the call saves a token
        # roundtrip on obvious blanks/garbage.
        return _fallback("OCR text too short to classify")

    client = get_openai_client()
    settings = get_settings()

    user_message = f"DOCUMENT LANGUAGE: {document_language}\nPAGE COUNT: {page_count}\n\nOCR TEXT:\n{text}"

    response = await client.chat.completions.create(
        model=settings.openai_classifier_model,
        temperature=0,
        max_tokens=settings.openai_classifier_max_output_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
    )

    raw = response.choices[0].message.content or "{}"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Classifier returned invalid JSON: %s | %s", exc, raw[:200])
        return _fallback("Invalid JSON from classifier")

    try:
        result = ClassifierResult.model_validate(data)
    except ValidationError as exc:
        logger.error("Classifier JSON failed validation: %s | data=%s", exc, data)
        return _fallback(f"Schema validation failed: {exc.errors()[0]['msg']}")

    # Enforce the "<0.5 → ostatni" rule prompt-side, but guard defensively here
    # in case the LLM ignores it.
    if result.confidence < 0.5 and result.category != "ostatni":
        result = result.model_copy(update={"category": "ostatni"})

    logger.info(
        "Classified category=%s doc_type=%s confidence=%.2f",
        result.category,
        result.doc_type,
        result.confidence,
    )
    return result
