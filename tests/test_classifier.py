"""Tests for classify_text — text-only GPT-4o classifier (no Vision)."""

import pytest

from app.prompts.classifier import CLASSIFIER_SYSTEM_PROMPT
from app.services.classifier import classify_text


def test_classifier_prompt_lists_all_categories():
    for category in (
        "danove",
        "socialni",
        "zdravotni",
        "cizinecka",
        "smlouvy",
        "ucty",
        "soudni",
        "urad_prace",
        "skolstvi",
        "ostatni",
    ):
        assert category in CLASSIFIER_SYSTEM_PROMPT


class TestClassifyText:
    @pytest.mark.asyncio
    async def test_classify_text_danove(self, openai_chat_mock):
        openai_chat_mock(
            {
                "category": "danove",
                "doc_type": "Platební výměr daně z příjmů",
                "sender": "Finanční úřad pro Prahu 3",
                "confidence": 0.95,
                "error": None,
            }
        )

        ocr = (
            "Platební výměr na daň z přidané hodnoty. Finanční úřad pro Prahu 3, "
            "DIČ CZ12345678, variabilní symbol 9876, částka k úhradě 4 320 Kč. "
            "Daňový subjekt je povinen uhradit nedoplatek na dani."
        )
        result = await classify_text(ocr, page_count=1, document_language="cs")
        assert result.category == "danove"
        assert result.doc_type == "Platební výměr daně z příjmů"
        assert result.confidence == 0.95
        assert result.error is None

    @pytest.mark.asyncio
    async def test_classify_text_low_confidence(self, openai_chat_mock):
        # The model returns a non-ostatni guess with confidence < 0.5;
        # the service should coerce it to ostatni.
        openai_chat_mock(
            {
                "category": "soudni",
                "doc_type": "neznámý",
                "sender": None,
                "confidence": 0.3,
                "error": None,
            }
        )

        ocr = " ".join(["lorem ipsum dolor sit amet consectetur"] * 5)
        result = await classify_text(ocr, page_count=1, document_language="cs")
        assert result.category == "ostatni"
        assert result.confidence < 0.5

    @pytest.mark.asyncio
    async def test_classify_text_too_short(self):
        # Skipped entirely — the service refuses to call the LLM.
        result = await classify_text("only five words here total", page_count=1, document_language="cs")
        assert result.category == "ostatni"
        assert result.confidence == 0.0
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_classify_empty_text(self):
        result = await classify_text("", page_count=1, document_language="cs")
        assert result.category == "ostatni"
        assert result.confidence == 0.0
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_invalid_json_returns_fallback(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="garbage {"))]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        monkeypatch.setattr("app.services.classifier.get_openai_client", lambda: mock_client)

        ocr = " ".join(["faktura splatnost zaplatit termín"] * 10)
        result = await classify_text(ocr, page_count=1, document_language="cs")
        assert result.category == "ostatni"
        assert result.confidence == 0.0
        assert "Invalid JSON" in (result.error or "")

    @pytest.mark.asyncio
    async def test_schema_violation_returns_fallback(self, openai_chat_mock):
        openai_chat_mock(
            {
                "category": "danove",
                "doc_type": "x",
                "confidence": 2.0,  # invalid range
            }
        )
        ocr = " ".join(["daň finanční úřad pdph nedoplatek"] * 10)
        result = await classify_text(ocr, page_count=1, document_language="cs")
        assert result.category == "ostatni"
        assert result.confidence == 0.0
