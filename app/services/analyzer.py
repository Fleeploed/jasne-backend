"""Analyzer service — call 2 (default or escalation analyzer).

Reads category-specific prompt module from `app.prompts.CATEGORY_MODULES`,
dispatches to the right model via `model_router`, and runs deadline output
through the `validator` to drop hallucinated dates.
"""

import json
import logging

from pydantic import ValidationError

from app.config import get_settings
from app.models.schemas import AnalysisResult, ClassifierResult, UserLanguage
from app.prompts import BASE_SYSTEM_PROMPT, CATEGORY_MODULES, OSTATNI_MODULE
from app.services.embeddings import get_openai_client
from app.services.model_router import pick_analyzer_model
from app.services.validator import filter_valid_deadlines

logger = logging.getLogger(__name__)


def _build_system_prompt(category: str, language: UserLanguage, has_datova_schranka: bool) -> str:
    module = CATEGORY_MODULES.get(category, OSTATNI_MODULE)
    return BASE_SYSTEM_PROMPT.format(
        language=language,
        has_datova_schranka="ano" if has_datova_schranka else "ne",
        category_module=module,
    )


async def analyze_document(
    classifier: ClassifierResult,
    rag_chunks: list[str],
    language: UserLanguage,
    has_datova_schranka: bool,
) -> tuple[AnalysisResult, str]:
    """Run the analyzer LLM and return (validated_result, model_used)."""
    settings = get_settings()
    client = get_openai_client()
    model = pick_analyzer_model(classifier.category)

    rag_context = (
        "\n\n---\n".join(rag_chunks)
        if rag_chunks
        else "Žádné další chunky z báze znalostí (RAG vrátil prázdno)."
    )

    user_message = (
        f"KATEGORIE: {classifier.category}\n"
        f"TYP DOKUMENTU: {classifier.doc_type}\n"
        f"ODESÍLATEL: {classifier.sender or 'neznámý'}\n"
        f"OCR CONFIDENCE: {classifier.confidence:.2f}\n"
        f"POČET STRAN: {classifier.page_count}\n\n"
        f"OCR TEXT:\n{classifier.ocr_text}\n\n"
        f"BÁZE ZNALOSTÍ (RAG):\n{rag_context}"
    )

    response = await client.chat.completions.create(
        model=model,
        temperature=0.2,
        max_tokens=settings.openai_max_output_tokens,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": _build_system_prompt(classifier.category, language, has_datova_schranka),
            },
            {"role": "user", "content": user_message},
        ],
    )

    raw = response.choices[0].message.content or "{}"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Analyzer returned invalid JSON: %s | %s", exc, raw[:200])
        raise ValueError("LLM returned invalid JSON") from exc

    try:
        result = AnalysisResult.model_validate(data)
    except ValidationError as exc:
        logger.error("Analyzer JSON failed validation: %s | data=%s", exc, data)
        raise ValueError(f"LLM output failed schema: {exc.errors()[0]['msg']}") from exc

    # Last-line defense against hallucinated deadlines.
    kept, rejected = filter_valid_deadlines(
        result.deadlines,
        source_text=classifier.ocr_text,
        category=classifier.category,
    )
    if rejected:
        logger.warning(
            "Validator dropped %d/%d deadlines: %s",
            len(rejected),
            len(result.deadlines),
            rejected,
        )
    result.deadlines = kept

    logger.info(
        "Analysis done model=%s category=%s confidence=%.2f deadlines=%d actions=%d",
        model,
        classifier.category,
        result.confidence,
        len(result.deadlines),
        len(result.actions_required),
    )
    return result, model
