"""Tests for ocr.classify_document with mocked OpenAI."""

import pytest

from app.services.ocr import classify_document


class TestClassifyDocument:
    @pytest.mark.asyncio
    async def test_happy_path(self, openai_chat_mock):
        openai_chat_mock(
            {
                "ocr_text": "Platební výměr č. 12345",
                "category": "danove",
                "doc_type": "platebni_vymer",
                "sender": "Finanční úřad",
                "document_language": "cs",
                "confidence": 0.95,
                "error": None,
            }
        )

        result = await classify_document("https://fake-url/img.jpg")

        assert result.category == "danove"
        assert result.doc_type == "platebni_vymer"
        assert result.confidence == 0.95
        assert "Platební výměr" in result.ocr_text

    @pytest.mark.asyncio
    async def test_invalid_json_returns_fallback(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="garbage {"))]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        monkeypatch.setattr("app.services.ocr.get_openai_client", lambda: mock_client)

        result = await classify_document("https://fake/img.jpg")

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
                "error": None,
            }
        )
        result = await classify_document("https://fake/img.jpg")
        assert result.category == "ostatni"
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_low_confidence_preserved(self, openai_chat_mock):
        openai_chat_mock(
            {
                "ocr_text": "",
                "category": "ostatni",
                "doc_type": "unreadable",
                "document_language": "cs",
                "confidence": 0.0,
                "error": "Photo too blurry",
            }
        )
        result = await classify_document("https://fake/img.jpg")
        assert result.confidence == 0.0
        assert result.error == "Photo too blurry"
