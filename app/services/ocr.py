import json
import logging

from pydantic import ValidationError

from app.config import get_settings
from app.models.schemas import ClassifierResult
from app.services.embeddings import get_openai_client

logger = logging.getLogger(__name__)

CLASSIFIER_SYSTEM_PROMPT = """\
Ты — Jasně OCR Classifier. Анализируй фото чешского документа и возвращай JSON.

ЗАДАЧИ:
1. Извлеки ВЕСЬ текст (OCR). Сохраняй диакритики (ř, č, š, ž, á, é, í, ó, ú, ů, ý),
   номера документов, IČO, DIČ, VS, суммы, даты.
2. Определи категорию (одну из 10 ниже).
3. Определи конкретный тип документа (doc_type, snake_case).
4. Извлеки отправителя (sender).

КАТЕГОРИИ:
- danove — Finanční úřad, daňové výměry, DPH, daně z příjmů
- socialni — ČSSZ, důchody, nemocenská, mateřská, OSVČ přehled
- zdravotni — VZP, OZP, ČPZP, RBP, ZPMV, VoZP
- cizinecka — OAMP, MV ČR, pobyt, víza, občanství
- smlouvy — nájemní, pracovní, úvěrové, pojistné, kupní
- ucty — faktury, utility, telco, SVJ vyúčtování
- soudni — soudy, exekuce, insolvence, platební rozkaz
- urad_prace — ÚP ČR, podpora, rekvalifikace, dávky
- skolstvi — školy, univerzity, stipendia
- ostatni — fallback

ПРАВИЛА OCR:
- Построчно, сохраняй структуру и таблицы
- НЕ переводи, НЕ перефразируй — только транскрипция
- Нечитаемое → [нечитаемо]
- Плохое фото → confidence = 0.0, ocr_text = "", error = описание

ФОРМАТ ОТВЕТА (строго JSON, все поля обязательны):
{
  "ocr_text": "...",
  "category": "danove",
  "doc_type": "platebni_vymer",
  "sender": "Finanční úřad pro Prahu 3",
  "document_language": "cs",
  "confidence": 0.95,
  "error": null
}
"""


def _fallback(reason: str) -> ClassifierResult:
    return ClassifierResult(
        ocr_text="",
        category="ostatni",
        doc_type="unknown",
        sender=None,
        document_language="cs",
        confidence=0.0,
        error=reason,
    )


async def classify_document(image_url: str) -> ClassifierResult:
    client = get_openai_client()
    settings = get_settings()

    response = await client.chat.completions.create(
        model=settings.openai_vision_model,
        temperature=0,
        max_tokens=settings.openai_max_output_tokens,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": CLASSIFIER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url, "detail": "high"},
                    }
                ],
            },
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

    logger.info(
        "Classified category=%s doc_type=%s confidence=%.2f lang=%s",
        result.category,
        result.doc_type,
        result.confidence,
        result.document_language,
    )
    return result
