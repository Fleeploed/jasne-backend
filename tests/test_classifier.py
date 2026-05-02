"""Tests for classifier.classify_document with mocked OpenAI (multi-page Vision)."""

import pytest

from app.prompts.classifier import CLASSIFIER_SYSTEM_PROMPT
from app.services.classifier import classify_document


def test_classifier_prompt_preserves_checkbox_state():
    assert "[x]" in CLASSIFIER_SYSTEM_PROMPT
    assert "[ ]" in CLASSIFIER_SYSTEM_PROMPT
    assert "bez poplatku (0 Kč)" in CLASSIFIER_SYSTEM_PROMPT


class TestClassifyDocument:
    @pytest.mark.asyncio
    async def test_happy_path_single_page(self, openai_chat_mock):
        openai_chat_mock(
            {
                "ocr_text": "Platební výměr č. 12345",
                "category": "danove",
                "doc_type": "platebni_vymer",
                "sender": "Finanční úřad",
                "document_language": "cs",
                "confidence": 0.95,
                "page_count": 1,
                "error": None,
            }
        )

        result = await classify_document(["https://fake-url/img.jpg"])

        assert result.category == "danove"
        assert result.doc_type == "platebni_vymer"
        assert result.confidence == 0.95
        assert result.page_count == 1
        assert "Platební výměr" in result.ocr_text

    @pytest.mark.asyncio
    async def test_happy_path_multi_page(self, openai_chat_mock):
        openai_chat_mock(
            {
                "ocr_text": "page 1\n\npage 2\n\npage 3",
                "category": "smlouvy",
                "doc_type": "najemni_smlouva",
                "sender": "Pronajímatel",
                "document_language": "cs",
                "confidence": 0.9,
                "page_count": 3,
                "error": None,
            }
        )

        urls = [f"https://fake-url/p{i}.jpg" for i in range(3)]
        result = await classify_document(urls)

        assert result.page_count == 3
        assert "page 2" in result.ocr_text

    @pytest.mark.asyncio
    async def test_empty_urls_returns_fallback(self):
        result = await classify_document([])
        assert result.category == "ostatni"
        assert result.confidence == 0.0
        assert "No image URLs" in (result.error or "")

    @pytest.mark.asyncio
    async def test_invalid_json_returns_fallback(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="garbage {"))]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        monkeypatch.setattr("app.services.classifier.get_openai_client", lambda: mock_client)

        result = await classify_document(["https://fake/img.jpg"])

        assert result.category == "ostatni"
        assert result.confidence == 0.0
        assert "Invalid JSON" in (result.error or "")

    @pytest.mark.asyncio
    async def test_schema_violation_returns_fallback(self, openai_chat_mock):
        # confidence out of range
        openai_chat_mock(
            {
                "ocr_text": "x",
                "category": "danove",
                "doc_type": "x",
                "document_language": "cs",
                "confidence": 2.0,  # invalid
                "page_count": 1,
                "error": None,
            }
        )
        result = await classify_document(["https://fake/img.jpg"])
        assert result.category == "ostatni"
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_missing_page_count_defaults_to_input_size(self, openai_chat_mock):
        openai_chat_mock(
            {
                "ocr_text": "x",
                "category": "ucty",
                "doc_type": "faktura",
                "document_language": "cs",
                "confidence": 0.8,
                # page_count missing — service should default to len(image_urls)
                "error": None,
            }
        )
        result = await classify_document(["https://a.jpg", "https://b.jpg", "https://c.jpg"])
        assert result.page_count == 3

    @pytest.mark.asyncio
    async def test_low_confidence_preserved(self, openai_chat_mock):
        openai_chat_mock(
            {
                "ocr_text": "",
                "category": "ostatni",
                "doc_type": "unreadable",
                "document_language": "cs",
                "confidence": 0.0,
                "page_count": 1,
                "error": "Photo too blurry",
            }
        )
        result = await classify_document(["https://fake/img.jpg"])
        assert result.confidence == 0.0
        assert result.error == "Photo too blurry"
