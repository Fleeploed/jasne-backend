"""Chat service — call 3 (Pro feature).

Q&A on top of an existing scan. Replays the entire `scan_messages` history
to GPT each turn (no server-side sessions in OpenAI's API). Cost grows with
conversation depth — limits service caps it at 10 questions per scan.
"""

import logging
from typing import Any
from uuid import UUID

from app.config import get_settings
from app.models.schemas import UserLanguage
from app.prompts import CHAT_SYSTEM_PROMPT
from app.services.embeddings import get_openai_client

logger = logging.getLogger(__name__)


def _build_scan_context_message(scan_row: dict[str, Any]) -> str:
    """Pack the scan's OCR + analyzer result into a single system message
    so the model has everything it needs to answer follow-ups."""
    ocr = scan_row.get("ocr_text") or ""
    result = scan_row.get("result") or {}

    summary = result.get("summary") or "(žádný summary)"
    deadlines = result.get("deadlines") or []
    actions = result.get("actions_required") or []
    penalty = result.get("penalty") or "(neuvedeno)"

    deadlines_str = (
        "\n".join(f"- {d.get('date')}: {d.get('description')} ({d.get('type')})" for d in deadlines)
        if deadlines
        else "(žádné lhůty)"
    )
    actions_str = "\n".join(f"- {a}" for a in actions) if actions else "(žádné akce)"

    return (
        "KONTEXT DOKUMENTU (z předchozí analýzy):\n\n"
        f"KATEGORIE: {scan_row.get('category')}\n"
        f"TYP: {scan_row.get('doc_type')}\n"
        f"ODESÍLATEL: {(result.get('sender')) or '(neznámý)'}\n\n"
        f"SUMMARY:\n{summary}\n\n"
        f"LHŮTY:\n{deadlines_str}\n\n"
        f"AKCE:\n{actions_str}\n\n"
        f"SANKCE:\n{penalty}\n\n"
        "OCR TEXT (přesný přepis dokumentu):\n"
        "---\n"
        f"{ocr}\n"
        "---"
    )


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

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": CHAT_SYSTEM_PROMPT.format(language=language),
        },
        {
            "role": "system",
            "content": _build_scan_context_message(scan_row),
        },
        *history,
        {"role": "user", "content": question},
    ]

    response = await client.chat.completions.create(
        model=settings.openai_chat_model,
        temperature=0.3,
        max_tokens=settings.openai_max_output_tokens,
        messages=messages,  # type: ignore[arg-type]
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
