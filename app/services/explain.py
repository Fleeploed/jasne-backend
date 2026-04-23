import json
import logging

from pydantic import ValidationError

from app.config import get_settings
from app.models.schemas import AnalysisResult, ClassifierResult, UserLanguage
from app.services.embeddings import get_openai_client

logger = logging.getLogger(__name__)

ESCALATION_CATEGORIES: set[str] = {"danove", "cizinecka", "soudni"}

_CATEGORY_MODULES: dict[str, str] = {
    "danove": """\
СПЕЦИФИКА: DAŇOVÉ (Finanční úřad)

ДЕДЛАЙНЫ:
- Platební výměr: 30 дней на оплату от doručení
- Odvolání: 30 дней от doručení (не от даты документа!)
- Daňové přiznání: до 1 апреля (или 1 июля с poradcem)

ОБЯЗАТЕЛЬНО:
- Číslo jednací / čj., Variabilní symbol (VS), номер účtu FÚ
- Сумма с разбивкой: daň, penále, úroky
- Zdaňovací období

ШТРАФЫ:
- Úrok z prodlení: 8% + repo sazba ČNB, с 5-го дня
- Penále: 0.05%/день, max 300 tis. Kč
- Exekuce после 30 дней просрочки
""",
    "cizinecka": """\
СПЕЦИФИКА: CIZINECKÁ (OAMP / MV ČR)

ДЕДЛАЙНЫ:
- Prodloužení pobytu: не позднее 14 дней до истечения
- Odpověď OAMP: 60 дней (сложные — до 180)
- Výzva k doložení: срок указан в документе

ОБЯЗАТЕЛЬНО:
- Číslo jednací / čj.
- Typ pobytu (přechodný, trvalý, dlouhodobý)
- Datum platnosti průkazu / víza
- Срок выполнения výzvy

ШТРАФЫ:
- Pobyt po lhůtě: до 3 лет запрет въезда
- Neoprávněný pobyt: pokuta до 100 tis. Kč
""",
    "ucty": """\
СПЕЦИФИКА: ÚČTY / FAKTURY

ДЕДЛАЙНЫ:
- Splatnost faktury — указана явно
- Upomínka: 7–14 дней

ОБЯЗАТЕЛЬНО:
- Číslo faktury / VS
- Částka celkem (с DPH и без)
- IBAN / číslo účtu + kód banky
- Datum splatnosti, dodavatel (IČO, adresa)

ШТРАФЫ:
- Úrok z prodlení: smluvní или zákonný (8% + repo)
- Upomínkový poplatek: указан в smlouvě
""",
}

_BASE_SYSTEM_PROMPT = """\
Ты — Jasně AI, эксперт по чешской бюрократии.

ПОДХОД:
1. Простой язык — как другу за кофе, не как юрист
2. Главный вопрос: «Что мне с этим делать?»
3. Дедлайны и суммы — ТОЛЬКО из OCR-текста
4. Checklist конкретных действий
5. Штрафы за бездействие (из OCR + базы знаний)
6. Суммы — "4 320 Kč" с пробелом между тысячами
7. Даты — ISO (YYYY-MM-DD) в JSON

ЯЗЫК ОТВЕТА: {language}
- Если язык ≠ cs → обязательно заполни translation
- Если язык = cs → translation = null
- Чешские термины (platební výměr, odvolání, VS, IČO) оставляй, поясняй при первом упоминании

DATOVÁ SCHRÁNKA пользователя: {has_datova_schranka}

ОГРАНИЧЕНИЯ:
- НЕ давай юридических советов → отправляй к právník / daňový poradce
- НЕ выдумывай дедлайны, суммы, VS — только из OCR или базы знаний
- Если OCR confidence < 0.7 → предупреди в поле tips

ФОРМАТ ОТВЕТА (строго JSON):
{{
  "document_type": "platebni_vymer",
  "document_type_cz": "Platební výměr",
  "document_type_localized": "... (null если language=cs)",
  "sender": "...",
  "summary": "...",
  "translation": "... (null если language=cs)",
  "deadlines": [{{"date": "YYYY-MM-DD", "description": "...", "type": "payment|action|response|expiration"}}],
  "actions_required": ["...", "..."],
  "penalty": "...",
  "where_to_go": "...",
  "documents_needed": ["..."],
  "confidence": 0.92,
  "tips": "..."
}}

{category_module}
"""


def pick_model(category: str) -> str:
    settings = get_settings()
    return (
        settings.openai_escalation_model
        if category in ESCALATION_CATEGORIES
        else settings.openai_vision_model
    )


def _build_system_prompt(category: str, language: UserLanguage, has_datova_schranka: bool) -> str:
    module_header = "СПЕЦИФИКА КАТЕГОРИИ:"
    module = _CATEGORY_MODULES.get(category, f"Общий подход для категории: {category}")
    return _BASE_SYSTEM_PROMPT.format(
        language=language,
        has_datova_schranka="да" if has_datova_schranka else "нет",
        category_module=f"{module_header}\n{module}",
    )


async def analyze_document(
    classifier: ClassifierResult,
    rag_chunks: list[str],
    language: UserLanguage,
    has_datova_schranka: bool,
) -> tuple[AnalysisResult, str]:
    settings = get_settings()
    client = get_openai_client()
    model = pick_model(classifier.category)

    rag_context = (
        "\n\n---\n".join(rag_chunks)
        if rag_chunks
        else "Нет дополнительного контекста (база знаний не найдена)."
    )

    user_message = (
        f"КАТЕГОРИЯ: {classifier.category}\n"
        f"ТИП ДОКУМЕНТА: {classifier.doc_type}\n"
        f"ОТПРАВИТЕЛЬ: {classifier.sender or 'неизвестен'}\n"
        f"OCR CONFIDENCE: {classifier.confidence:.2f}\n\n"
        f"OCR-ТЕКСТ:\n{classifier.ocr_text}\n\n"
        f"БАЗА ЗНАНИЙ (RAG):\n{rag_context}"
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

    logger.info(
        "Analysis done model=%s category=%s confidence=%.2f deadlines=%d actions=%d",
        model,
        classifier.category,
        result.confidence,
        len(result.deadlines),
        len(result.actions_required),
    )
    return result, model
