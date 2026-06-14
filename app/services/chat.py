"""Chat service — call 3 (Pro feature).

Q&A on top of an existing scan. Replays the entire `scan_messages` history
to GPT each turn (no server-side sessions in OpenAI's API). Cost grows with
conversation depth — limits service caps it at 10 questions per scan.
"""

import logging
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from app.config import get_settings
from app.models.schemas import UserLanguage
from app.prompts import CHAT_SYSTEM_PROMPT
from app.services.embeddings import get_openai_client
from app.services.fee_guards import filter_fee_items, should_suppress_fee_guidance
from app.services.rag import get_supabase
from app.services.token_usage import extract_usage, log_usage

logger = logging.getLogger(__name__)


def _scan_row_should_suppress_fee_guidance(scan_row: dict[str, Any]) -> bool:
    return should_suppress_fee_guidance(
        str(scan_row.get("doc_type") or ""),
        str(scan_row.get("ocr_text") or ""),
    )


def _build_scan_context_message(scan_row: dict[str, Any]) -> str:
    """Pack the scan's OCR + analyzer result into a single system message
    so the model has everything it needs to answer follow-ups."""
    ocr = scan_row.get("ocr_text") or ""
    result = scan_row.get("result") or {}

    summary = result.get("summary") or "(žádný summary)"
    deadlines = result.get("deadlines") or []
    actions = result.get("actions_required") or []
    penalty = result.get("penalty") or "(neuvedeno)"
    office_hours = result.get("office_hours") or "(neuvedeno)"
    documents_needed = result.get("documents_needed") or []

    suppress_fee_guidance = _scan_row_should_suppress_fee_guidance(scan_row)
    if suppress_fee_guidance:
        actions = filter_fee_items(actions)
        documents_needed = filter_fee_items(documents_needed)

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
        f"ÚŘEDNÍ HODINY:\n{office_hours}\n\n"
        f"{fee_guard}"
        "OCR TEXT (přesný přepis dokumentu):\n"
        "---\n"
        f"{ocr}\n"
        "---"
    )


def _build_knowledge_context_message(rag_chunks: list[str] | None) -> str:
    if not rag_chunks:
        return (
            "BÁZE ZNALOSTÍ (RAG): Pro tuto otázku nebyly nalezeny žádné relevantní "
            "chunky. Pokud odpověď není v OCR/result/history, řekni, že ji v kontextu nemáš."
        )

    chunks = "\n\n".join(f"[RAG {index}]\n{chunk}" for index, chunk in enumerate(rag_chunks, start=1))
    return (
        "BÁZE ZNALOSTÍ (RAG) PRO AKTUÁLNÍ OTÁZKU:\n"
        "Použij tyto chunky, pokud odpovídají otázce uživatele. Hodí se hlavně pro "
        "obecné informace, adresy, kontakty, úřední hodiny a postupy. "
        "Když se uživatel ptá na hodiny práce / úřední hodiny / opening hours "
        "a RAG obsahuje hodiny pro adresu z dokumentu, odpověz z RAG; neříkej, "
        "že hodiny nejsou v dokumentu. "
        "Pokud se chunky liší, preferuj dříve uvedený chunk s přesnou adresou "
        "nebo konkrétní kontaktní kartou. "
        "Konkrétní lhůty, částky a povinnosti z dokumentu ber z OCR/result, ne z RAG.\n\n"
        f"{chunks}"
    )


def _build_chat_messages(
    scan_row: dict[str, Any],
    history: list[dict[str, str]],
    question: str,
    language: UserLanguage,
    rag_chunks: list[str] | None = None,
) -> list[dict[str, str]]:
    history_context = (
        "HISTORIE CHATU: Následující user/assistant zprávy jsou skutečná "
        "dosavadní historie tohoto chatu. Použij je pro návazné otázky."
        if history
        else "HISTORIE CHATU: Zatím žádná předchozí textová historie."
    )
    return [
        {
            "role": "system",
            "content": CHAT_SYSTEM_PROMPT.format(language=language, scan_language=scan_row.get("language")),
        },
        {
            "role": "system",
            "content": _build_scan_context_message(scan_row),
        },
        {
            "role": "system",
            "content": _build_knowledge_context_message(rag_chunks),
        },
        {
            "role": "system",
            "content": history_context,
        },
        *history,
        {"role": "user", "content": question},
    ]


async def answer_question(
    scan_row: dict[str, Any],
    history: list[dict[str, str]],
    question: str,
    language: UserLanguage,
    rag_chunks: list[str] | None = None,
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
        messages=_build_chat_messages(scan_row, history, question, language, rag_chunks),  # type: ignore[arg-type]
    )

    log_usage("chat", settings.openai_chat_model, extract_usage(response), scan_id=scan_row.get("id"))
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
    rag_chunks: list[str] | None = None,
) -> AsyncIterator[str]:
    """Yield assistant answer chunks as OpenAI streams them."""
    settings = get_settings()
    client = get_openai_client()

    stream = await client.chat.completions.create(
        model=settings.openai_chat_model,
        temperature=0.3,
        max_tokens=settings.openai_max_output_tokens,
        stream=True,
        stream_options={"include_usage": True},
        messages=_build_chat_messages(scan_row, history, question, language, rag_chunks),  # type: ignore[arg-type]
    )

    chunk_count = 0
    stream_usage: dict[str, int] | None = None
    async for event in stream:
        usage = extract_usage(event)
        if usage is not None:
            stream_usage = usage
        if not event.choices:
            continue
        delta = event.choices[0].delta.content or ""
        if not delta:
            continue
        chunk_count += 1
        yield delta

    log_usage("chat_stream", settings.openai_chat_model, stream_usage, scan_id=scan_row.get("id"))
    logger.info(
        "Chat streamed scan_id=%s history=%d question_len=%d chunks=%d",
        scan_row.get("id"),
        len(history),
        len(question),
        chunk_count,
    )


async def fetch_scan_history(scan_id: UUID, user_id: UUID) -> list[dict[str, str]]:
    """Load all chat messages for a scan, oldest first, formatted for OpenAI."""
    supabase = await get_supabase()
    result = (
        await supabase.table("scan_messages")
        .select("id,role,content,message_type,seq")
        .eq("scan_id", str(scan_id))
        .eq("user_id", str(user_id))
        .order("seq")
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
