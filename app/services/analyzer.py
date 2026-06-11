"""Analyzer service — call 2 (default or escalation analyzer).

Reads category-specific prompt module from `app.prompts.CATEGORY_MODULES`,
dispatches to the right model via `model_router`, and runs deadline output
through the `validator` to drop hallucinated dates.
"""

import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.models.schemas import AnalysisResult, ClassifierResult, UserLanguage
from app.prompts import BASE_SYSTEM_PROMPT, CATEGORY_MODULES, LANGUAGE_MODULES, OSTATNI_MODULE
from app.services.embeddings import get_openai_client
from app.services.fee_guards import (
    filter_fee_items,
    mentions_fee,
    should_suppress_fee_guidance,
)
from app.services.model_router import pick_analyzer_model
from app.services.validator import detect_relative_deadline_days, filter_valid_deadlines

logger = logging.getLogger(__name__)

STREAM_ANALYSIS_FIELDS: tuple[str, ...] = (
    "summary",
    "translation",
    "actions_required",
    "penalty",
    "where_to_go",
    "office_hours",
    "documents_needed",
    "tips",
    "deadlines",
)

# Deadlines must never reach the client before filter_valid_deadlines runs on
# the full result — a streamed raw deadline could flash a hallucinated or past
# date that later disappears. They are emitted only post-validation.
STREAM_INCREMENTAL_FIELDS: tuple[str, ...] = tuple(
    field for field in STREAM_ANALYSIS_FIELDS if field != "deadlines"
)

_CONFIRMATION_ACTION_BLOCKLIST = (
    "dolož",
    "dopln",
    "předlož",
    "predloz",
    "přinést",
    "prines",
    "podat žádost",
    "podat zadost",
    "zaplat",
    "submit",
    "provide",
    "bring",
    "додати",
    "подати заяв",
    "предостав",
    "донест",
    "оплат",
)


def _looks_like_submission_confirmation(classifier: ClassifierResult, ocr_text: str) -> bool:
    doc_type = classifier.doc_type.lower()
    ocr = ocr_text.lower()
    return (
        "potvrzeni_o_podani_zadosti" in doc_type
        or "potvrzeni o podani zadosti" in doc_type
        or "potvrzení o podání žádosti" in ocr
    )


def _classifier_should_suppress_fee_guidance(classifier: ClassifierResult, ocr_text: str) -> bool:
    return should_suppress_fee_guidance(classifier.doc_type, ocr_text)


def _strip_unconfirmed_fee_text(text: str | None) -> str | None:
    if not text:
        return text

    cleaned = re.sub(
        r"\s+(?:a|,)\s+(?:uhradit|zaplatit)\s+[^.]*?(?:kolk\w*|poplat\w*)[^.]*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    sentences = re.split(r"(?<=[.!?])\s+", cleaned.strip())
    kept = [sentence for sentence in sentences if sentence and not mentions_fee(sentence)]
    return " ".join(kept).strip() or None


def _confirmation_followup_action(language: UserLanguage) -> str:
    if language == "en":
        return "Keep the confirmation and follow the application status through IPC."
    if language == "uk":
        return "Збережіть підтвердження та відстежуйте стан заяви через IPC."
    if language == "ru":
        return "Сохраните подтверждение и отслеживайте статус заявления через IPC."
    return "Uschovat potvrzení a sledovat stav žádosti přes IPC."


def _clean_confirmation_actions(actions: list[str], language: UserLanguage) -> list[str]:
    kept = [
        action
        for action in actions
        if not any(blocked in action.lower() for blocked in _CONFIRMATION_ACTION_BLOCKLIST)
    ]
    return kept or [_confirmation_followup_action(language)]


def _apply_card_pickup_fee_guardrail(
    result: AnalysisResult, classifier: ClassifierResult, ocr_text: str
) -> AnalysisResult:
    if not _classifier_should_suppress_fee_guidance(classifier, ocr_text):
        return result

    result.summary = _strip_unconfirmed_fee_text(result.summary) or result.summary
    result.tips = _strip_unconfirmed_fee_text(result.tips)
    result.actions_required = filter_fee_items(result.actions_required)
    result.documents_needed = filter_fee_items(result.documents_needed)
    return result


def _apply_analysis_guardrails(
    result: AnalysisResult,
    classifier: ClassifierResult,
    ocr_text: str,
    language: UserLanguage,
) -> AnalysisResult:
    """Last-line defense against RAG-driven hallucinations for known doc types."""
    result = _apply_card_pickup_fee_guardrail(result, classifier, ocr_text)

    if not _looks_like_submission_confirmation(classifier, ocr_text):
        return result

    result.documents_needed = []
    result.penalty = None
    result.deadlines = []
    result.actions_required = _clean_confirmation_actions(result.actions_required, language)
    return result


def _sanitize_stream_field(
    field: str,
    value: Any,
    classifier: ClassifierResult,
    ocr_text: str,
    language: UserLanguage,
) -> Any:
    if not _looks_like_submission_confirmation(classifier, ocr_text):
        if not _classifier_should_suppress_fee_guidance(classifier, ocr_text):
            return value
        if field in {"summary", "tips"} and isinstance(value, str):
            return _strip_unconfirmed_fee_text(value)
        if field in {"actions_required", "documents_needed"} and isinstance(value, list):
            return filter_fee_items(value)
        return value
    if field == "documents_needed":
        return []
    if field in {"penalty", "deadlines"}:
        return None if field == "penalty" else []
    if field == "actions_required" and isinstance(value, list):
        return _clean_confirmation_actions([str(action) for action in value], language)
    return value


def _build_system_prompt(category: str, language: UserLanguage, has_datova_schranka: bool) -> str:
    module = CATEGORY_MODULES.get(category, OSTATNI_MODULE)
    language_module = LANGUAGE_MODULES[language]
    return BASE_SYSTEM_PROMPT.format(
        language=language,
        language_module=language_module,
        has_datova_schranka="ano" if has_datova_schranka else "ne",
        category_module=module,
    )


def _build_user_message(
    classifier: ClassifierResult,
    ocr_text: str,
    page_count: int,
    rag_chunks: list[str],
    language: UserLanguage,
) -> str:
    rag_context = (
        "\n\n---\n".join(rag_chunks)
        if rag_chunks
        else "Žádné další chunky z báze znalostí (RAG vrátil prázdno)."
    )

    return (
        f"KATEGORIE: {classifier.category}\n"
        f"TYP DOKUMENTU: {classifier.doc_type}\n"
        f"ODESÍLATEL: {classifier.sender or 'neznámý'}\n"
        f"OCR CONFIDENCE: {classifier.confidence:.2f}\n"
        f"POČET STRAN: {page_count}\n\n"
        f"CÍLOVÝ JAZYK VÝSTUPU: {language}\n"
        "POZOR: OCR dokument může být česky, ale uživatelská textová pole "
        "musí být v cílovém jazyce výstupu.\n\n"
        f"OCR TEXT:\n{ocr_text}\n\n"
        f"BÁZE ZNALOSTÍ (RAG):\n{rag_context}"
    )


def _build_messages(
    classifier: ClassifierResult,
    ocr_text: str,
    page_count: int,
    rag_chunks: list[str],
    language: UserLanguage,
    has_datova_schranka: bool,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": _build_system_prompt(classifier.category, language, has_datova_schranka),
        },
        {
            "role": "user",
            "content": _build_user_message(classifier, ocr_text, page_count, rag_chunks, language),
        },
    ]


def _extract_completed_json_fields(
    buffer: str,
    emitted: set[str],
    fields: tuple[str, ...] = STREAM_ANALYSIS_FIELDS,
) -> dict[str, Any]:
    """Best-effort extraction of completed top-level JSON fields from a stream."""
    decoder = json.JSONDecoder()
    extracted: dict[str, Any] = {}
    for field in fields:
        if field in emitted:
            continue
        match = re.search(rf'"{re.escape(field)}"\s*:', buffer)
        if not match:
            continue
        value_start = match.end()
        while value_start < len(buffer) and buffer[value_start].isspace():
            value_start += 1
        try:
            value, _ = decoder.raw_decode(buffer, value_start)
        except json.JSONDecodeError:
            continue
        extracted[field] = value
    return extracted


def _parse_analysis_result(raw: str) -> AnalysisResult:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Analyzer returned invalid JSON: %s | %s", exc, raw[:200])
        raise ValueError("LLM returned invalid JSON") from exc

    try:
        return AnalysisResult.model_validate(data)
    except ValidationError as exc:
        logger.error("Analyzer JSON failed validation: %s | data=%s", exc, data)
        raise ValueError(f"LLM output failed schema: {exc.errors()[0]['msg']}") from exc


def _relative_deadline_tip(days: int, language: UserLanguage) -> str:
    if language == "en":
        return (
            f"The document sets a deadline of {days} days from delivery/receipt. "
            "We can't auto-calculate the date, so add a manual reminder once you "
            "know the delivery date."
        )
    if language == "uk":
        return (
            f"У документі є строк {days} днів від доручення. Точну дату ми не можемо "
            "розрахувати автоматично — додайте нагадування вручну, коли знатимете дату вручення."
        )
    if language == "ru":
        return (
            f"В документе указан срок {days} дней с момента вручения. Точную дату мы не можем "
            "рассчитать автоматически — добавьте напоминание вручную, когда узнаете дату вручения."
        )
    return (
        f"Dokument uvádí lhůtu {days} dnů od doručení. Přesné datum neumíme spočítat "
        "automaticky — až budete znát datum doručení, přidejte si připomínku ručně."
    )


def _append_relative_deadline_tip(
    result: AnalysisResult, ocr_text: str, language: UserLanguage
) -> AnalysisResult:
    days = detect_relative_deadline_days(ocr_text)
    if days is None:
        return result
    tip = _relative_deadline_tip(days, language)
    existing = (result.tips or "").strip()
    if tip in existing:
        return result
    result.tips = f"{existing}\n{tip}".strip() if existing else tip
    return result


def _filter_deadlines(
    result: AnalysisResult,
    classifier: ClassifierResult,
    ocr_text: str,
    language: UserLanguage,
) -> AnalysisResult:
    # Last-line defense against hallucinated deadlines.
    kept, rejected = filter_valid_deadlines(
        result.deadlines,
        source_text=ocr_text,
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
    # Relative deadlines never produce a valid absolute date; surface them as a
    # manual-reminder tip instead of dropping silently. Skip ostatni (no auto
    # deadlines by design).
    if classifier.category != "ostatni":
        result = _append_relative_deadline_tip(result, ocr_text, language)
    return result


async def analyze_document(
    classifier: ClassifierResult,
    ocr_text: str,
    page_count: int,
    rag_chunks: list[str],
    language: UserLanguage,
    has_datova_schranka: bool,
) -> tuple[AnalysisResult, str]:
    """Run the analyzer LLM and return (validated_result, model_used)."""
    settings = get_settings()
    client = get_openai_client()
    model = pick_analyzer_model(classifier.category)

    response = await client.chat.completions.create(
        model=model,
        temperature=0.2,
        max_tokens=settings.openai_analyzer_max_output_tokens,
        response_format={"type": "json_object"},
        messages=_build_messages(classifier, ocr_text, page_count, rag_chunks, language, has_datova_schranka),
    )

    raw = response.choices[0].message.content or "{}"
    result = _apply_analysis_guardrails(
        _filter_deadlines(_parse_analysis_result(raw), classifier, ocr_text, language),
        classifier,
        ocr_text,
        language,
    )

    logger.info(
        "Analysis done model=%s category=%s confidence=%.2f deadlines=%d actions=%d",
        model,
        classifier.category,
        result.confidence,
        len(result.deadlines),
        len(result.actions_required),
    )
    return result, model


async def stream_analyze_document(
    classifier: ClassifierResult,
    ocr_text: str,
    page_count: int,
    rag_chunks: list[str],
    language: UserLanguage,
    has_datova_schranka: bool,
) -> AsyncIterator[dict[str, Any]]:
    """Stream completed analyzer fields, then yield the validated full result."""
    settings = get_settings()
    client = get_openai_client()
    model = pick_analyzer_model(classifier.category)

    stream = await client.chat.completions.create(
        model=model,
        temperature=0.2,
        max_tokens=settings.openai_analyzer_max_output_tokens,
        response_format={"type": "json_object"},
        stream=True,
        messages=_build_messages(classifier, ocr_text, page_count, rag_chunks, language, has_datova_schranka),
    )

    emitted: set[str] = set()
    raw_parts: list[str] = []
    async for event in stream:
        if not event.choices:
            continue
        delta = event.choices[0].delta.content or ""
        if not delta:
            continue
        raw_parts.append(delta)
        buffer = "".join(raw_parts)
        for field, value in _extract_completed_json_fields(
            buffer, emitted, fields=STREAM_INCREMENTAL_FIELDS
        ).items():
            emitted.add(field)
            yield {
                "type": "field",
                "field": field,
                "value": _sanitize_stream_field(field, value, classifier, ocr_text, language),
            }

    raw = "".join(raw_parts) or "{}"
    result = _apply_analysis_guardrails(
        _filter_deadlines(_parse_analysis_result(raw), classifier, ocr_text, language),
        classifier,
        ocr_text,
        language,
    )
    data = result.model_dump(mode="json")
    for field in STREAM_ANALYSIS_FIELDS:
        if field not in emitted:
            emitted.add(field)
            yield {"type": "field", "field": field, "value": data.get(field)}

    logger.info(
        "Analysis streamed model=%s category=%s confidence=%.2f deadlines=%d actions=%d fields=%d",
        model,
        classifier.category,
        result.confidence,
        len(result.deadlines),
        len(result.actions_required),
        len(emitted),
    )
    yield {"type": "done", "analysis": result, "model_used": model}
