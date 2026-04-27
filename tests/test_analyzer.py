"""Tests for analyzer service: prompt building + analyze_document with mocked OpenAI."""

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.schemas import ClassifierResult
from app.services.analyzer import _build_system_prompt, analyze_document


class TestBuildSystemPrompt:
    def test_contains_language(self):
        prompt = _build_system_prompt("danove", "ru", False)
        assert "JAZYK ODPOVĚDI: ru" in prompt

    def test_datova_schranka_yes(self):
        prompt = _build_system_prompt("ucty", "cs", True)
        assert "DATOVÁ SCHRÁNKA uživatele: ano" in prompt

    def test_datova_schranka_no(self):
        prompt = _build_system_prompt("ucty", "cs", False)
        assert "DATOVÁ SCHRÁNKA uživatele: ne" in prompt

    def test_contains_category_module_for_known_category(self):
        prompt = _build_system_prompt("danove", "cs", False)
        assert "Finanční úřad" in prompt
        assert "Platební výměr" in prompt

    def test_unknown_category_falls_back_to_ostatni(self):
        prompt = _build_system_prompt("totally_made_up", "cs", False)
        assert "OSTATNÍ" in prompt
        # OSTATNI module instructs to NOT auto-create deadlines
        assert "deadlines = []" in prompt


class TestAnalyzeDocument:
    @pytest.mark.asyncio
    async def test_happy_path_escalation_model(self, openai_chat_mock):
        future = date.today() + timedelta(days=30)
        openai_chat_mock(
            {
                "document_type": "platebni_vymer",
                "document_type_cz": "Platební výměr",
                "document_type_localized": None,
                "sender": "FÚ",
                "summary": "summary",
                "translation": None,
                "deadlines": [{"date": future.isoformat(), "description": "Pay", "type": "payment"}],
                "actions_required": ["pay"],
                "penalty": None,
                "where_to_go": None,
                "documents_needed": [],
                "confidence": 0.9,
                "tips": None,
            }
        )

        classifier = ClassifierResult(
            ocr_text="hello",
            category="danove",  # → escalation
            doc_type="platebni_vymer",
            sender="FÚ",
            document_language="cs",
            confidence=0.9,
            page_count=1,
        )
        result, model = await analyze_document(classifier, [], "cs", False)

        assert result.document_type == "platebni_vymer"
        assert len(result.deadlines) == 1  # validator kept the future deadline
        # Escalation model is "gpt-4o" by default config
        assert model == "gpt-4o"

    @pytest.mark.asyncio
    async def test_default_model_for_routine_category(self, openai_chat_mock):
        openai_chat_mock(
            {
                "document_type": "faktura",
                "document_type_cz": "Faktura",
                "summary": "pay",
                "deadlines": [],
                "actions_required": [],
                "documents_needed": [],
                "confidence": 0.8,
            }
        )
        classifier = ClassifierResult(
            ocr_text="x",
            category="ucty",  # → default model
            doc_type="faktura",
            document_language="cs",
            confidence=0.8,
            page_count=1,
        )
        _, model = await analyze_document(classifier, [], "cs", False)
        assert model == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_validator_drops_past_deadlines(self, openai_chat_mock):
        past = date.today() - timedelta(days=10)
        future = date.today() + timedelta(days=10)
        openai_chat_mock(
            {
                "document_type": "x",
                "document_type_cz": "X",
                "summary": "s",
                "deadlines": [
                    {"date": past.isoformat(), "description": "old", "type": "action"},
                    {"date": future.isoformat(), "description": "new", "type": "payment"},
                ],
                "actions_required": [],
                "documents_needed": [],
                "confidence": 0.9,
            }
        )
        classifier = ClassifierResult(
            ocr_text="x",
            category="ucty",
            doc_type="faktura",
            document_language="cs",
            confidence=0.9,
            page_count=1,
        )
        result, _ = await analyze_document(classifier, [], "cs", False)
        # Past deadline filtered out, future kept
        assert len(result.deadlines) == 1
        assert result.deadlines[0].description == "new"

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="not json"))]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        monkeypatch.setattr("app.services.analyzer.get_openai_client", lambda: mock_client)

        classifier = ClassifierResult(
            ocr_text="x",
            category="ucty",
            doc_type="faktura",
            document_language="cs",
            confidence=0.8,
            page_count=1,
        )
        with pytest.raises(ValueError, match="invalid JSON"):
            await analyze_document(classifier, [], "cs", False)

    @pytest.mark.asyncio
    async def test_schema_violation_raises(self, openai_chat_mock):
        openai_chat_mock(
            {
                "document_type": "x",
                "document_type_cz": "X",
                "summary": "s",
                "confidence": 5.0,  # out of range
            }
        )
        classifier = ClassifierResult(
            ocr_text="x",
            category="ucty",
            doc_type="faktura",
            document_language="cs",
            confidence=0.8,
            page_count=1,
        )
        with pytest.raises(ValueError, match="schema"):
            await analyze_document(classifier, [], "cs", False)
