"""Chat service — call 3 (Pro feature).

Q&A on top of an existing scan. Replays the entire `scan_messages` history
to GPT each turn (no server-side sessions in OpenAI's API). Cost grows with
conversation depth — limits service caps it at 10 questions per scan.
"""

import logging
import re
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from app.config import get_settings
from app.models.schemas import UserLanguage
from app.prompts import CHAT_SYSTEM_PROMPT
from app.services.embeddings import get_openai_client

logger = logging.getLogger(__name__)

_FEE_TERMS = (
    "poplatek",
    "poplatku",
    "kolk",
    "kolku",
    "kolky",
    "uhradit",
    "zaplatit",
    "fee",
    "payment",
    "stamp",
    "оплат",
    "платеж",
    "платіж",
    "колк",
)


def _ascii_fold(text: str) -> str:
    replacements = str.maketrans(
        {
            "á": "a",
            "č": "c",
            "ď": "d",
            "é": "e",
            "ě": "e",
            "í": "i",
            "ň": "n",
            "ó": "o",
            "ř": "r",
            "š": "s",
            "ť": "t",
            "ú": "u",
            "ů": "u",
            "ý": "y",
            "ž": "z",
        }
    )
    return text.lower().translate(replacements)


def _looks_like_card_pickup_notice(scan_row: dict[str, Any]) -> bool:
    doc_type = _ascii_fold(str(scan_row.get("doc_type") or ""))
    ocr = _ascii_fold(str(scan_row.get("ocr_text") or ""))
    return (
        "vyzva_k_prevzeti_prukazu" in doc_type
        or "vyzva k prevzeti prukazu" in ocr
        or "prevzeti prukazu o povoleni k dlouhodobemu pobytu" in ocr
    )


def _has_checked_positive_fee(ocr_text: str) -> bool:
    text = _ascii_fold(ocr_text)
    checked = r"(?:\[x\]|\(x\)|☒|✅|✓|✔|x)"
    amount = r"(?:300|1\s*000|1000|2\s*500|2500)\s*(?:kc|kč)"
    return bool(re.search(rf"(?:{checked}\s*{amount})|(?:{amount}\s*{checked})", text))


def _should_suppress_fee_guidance(scan_row: dict[str, Any]) -> bool:
    if not _looks_like_card_pickup_notice(scan_row):
        return False
    return not _has_checked_positive_fee(str(scan_row.get("ocr_text") or ""))


def _mentions_fee(text: str) -> bool:
    folded = _ascii_fold(text)
    lowered = text.lower()
    return any(term in folded or term in lowered for term in _FEE_TERMS)


def _filter_fee_items(items: list[Any]) -> list[Any]:
    return [item for item in items if not _mentions_fee(str(item))]


def _build_scan_context_message(scan_row: dict[str, Any]) -> str:
    """Pack the scan's OCR + analyzer result into a single system message
    so the model has everything it needs to answer follow-ups."""
    ocr = scan_row.get("ocr_text") or ""
    result = scan_row.get("result") or {}

    summary = result.get("summary") or "(žádný summary)"
    deadlines = result.get("deadlines") or []
    actions = result.get("actions_required") or []
    penalty = result.get("penalty") or "(neuvedeno)"
    documents_needed = result.get("documents_needed") or []

    suppress_fee_guidance = _should_suppress_fee_guidance(scan_row)
    if suppress_fee_guidance:
        actions = _filter_fee_items(actions)
        documents_needed = _filter_fee_items(documents_needed)

    deadlines_str = (
        "\n".join(f"- {d.get('date')}: {d.get('description')} ({d.get('type')})" for d in deadlines)
        if deadlines
        else "(žádné lhůty)"
    )
    actions_str = "\n".join(f"- {a}" for a in actions) if actions else "(žádné akce)"
    documents_needed_str = (
        "\n".join(f"- {doc}" for doc in documents_needed) if documents_needed else "(žádné dokumenty)"
    )
    fee_guard = (
        "\nPOPLATEK/KOLKY: V OCR není jednoznačně vybraná částka větší než 0 Kč. "
        "Neříkej uživateli, že má platit nebo připravit kolky, pokud se ptá na tento dokument.\n"
        if suppress_fee_guidance
        else ""
    )

    return (
        "KONTEXT DOKUMENTU (z předchozí analýzy):\n\n"
        f"JAZYK SKENU: {scan_row.get('language') or '(neznámý)'}\n"
        f"KATEGORIE: {scan_row.get('category')}\n"
        f"TYP: {scan_row.get('doc_type')}\n"
        f"ODESÍLATEL: {(result.get('sender')) or '(neznámý)'}\n\n"
        f"SUMMARY:\n{summary}\n\n"
        f"LHŮTY:\n{deadlines_str}\n\n"
        f"AKCE:\n{actions_str}\n\n"
        f"DOKUMENTY POTŘEBNÉ:\n{documents_needed_str}\n\n"
        f"SANKCE:\n{penalty}\n\n"
        f"{fee_guard}"
        "OCR TEXT (přesný přepis dokumentu):\n"
        "---\n"
        f"{ocr}\n"
        "---"
    )


def _build_chat_messages(
    scan_row: dict[str, Any],
    history: list[dict[str, str]],
    question: str,
    language: UserLanguage,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": CHAT_SYSTEM_PROMPT.format(language=language, scan_language=scan_row.get("language")),
        },
        {
            "role": "system",
            "content": _build_scan_context_message(scan_row),
        },
        *history,
        {"role": "user", "content": question},
    ]


async def answer_question(
    scan_row: dict[str, Any],
    history: list[dict[str, str]],
    question: str,
    language: UserLanguage,
) -> str:
    """Run the chat LLM and return assistant's text answer.

    `history` is a list of {"role": "user"|"assistant", "content": "..."}
    in chronological order (excluding the new question, which we append
    inside this function).
    """
    settings = get_settings()
    client = get_openai_client()

    response = await client.chat.completions.create(
        model=settings.openai_chat_model,
        temperature=0.3,
        max_tokens=settings.openai_max_output_tokens,
        messages=_build_chat_messages(scan_row, history, question, language),  # type: ignore[arg-type]
    )

    answer = (response.choices[0].message.content or "").strip()
    if not answer:
        # Defensive — never persist empty assistant message
        answer = "Bohužel jsem nemohl odpovědět. Zkuste prosím přeformulovat otázku."

    logger.info(
        "Chat answered scan_id=%s history=%d question_len=%d answer_len=%d",
        scan_row.get("id"),
        len(history),
        len(question),
        len(answer),
    )
    return answer


async def stream_answer_chunks(
    scan_row: dict[str, Any],
    history: list[dict[str, str]],
    question: str,
    language: UserLanguage,
) -> AsyncIterator[str]:
    """Yield assistant answer chunks as OpenAI streams them."""
    settings = get_settings()
    client = get_openai_client()

    stream = await client.chat.completions.create(
        model=settings.openai_chat_model,
        temperature=0.3,
        max_tokens=settings.openai_max_output_tokens,
        stream=True,
        messages=_build_chat_messages(scan_row, history, question, language),  # type: ignore[arg-type]
    )

    chunk_count = 0
    async for event in stream:
        if not event.choices:
            continue
        delta = event.choices[0].delta.content or ""
        if not delta:
            continue
        chunk_count += 1
        yield delta

    logger.info(
        "Chat streamed scan_id=%s history=%d question_len=%d chunks=%d",
        scan_row.get("id"),
        len(history),
        len(question),
        chunk_count,
    )


async def fetch_scan_history(scan_id: UUID, user_id: UUID) -> list[dict[str, str]]:
    """Load all chat messages for a scan, oldest first, formatted for OpenAI."""
    from app.services.rag import get_supabase

    supabase = await get_supabase()
    result = (
        await supabase.table("scan_messages")
        .select("role,content,message_type,created_at")
        .eq("scan_id", str(scan_id))
        .eq("user_id", str(user_id))
        .order("created_at")
        .execute()
    )

    out: list[dict[str, str]] = []
    for row in result.data or []:
        # Skip the initial image + result_card rows — they're shown in the UI
        # but the OCR + result are already injected via _build_scan_context_message.
        if row.get("message_type") in {"image", "result_card"}:
            continue
        out.append({"role": row["role"], "content": row["content"]})
    return out
