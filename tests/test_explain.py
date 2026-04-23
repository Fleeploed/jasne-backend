"""Tests for pure functions in explain service + mocked analyze_document."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.schemas import ClassifierResult
from app.services.explain import (
    ESCALATION_CATEGORIES,
    _build_system_prompt,
    analyze_document,
    pick_model,
)


class TestPickModel:
    def test_escalation_category_uses_escalation_model(self):
        for cat in ESCALATION_CATEGORIES:
            model = pick_model(cat)
            assert model == "gpt-4o"

    def test_normal_category_uses_default(self):
        assert pick_model("ucty") == "gpt-4o-mini"
        assert pick_model("zdravotni") == "gpt-4o-mini"
        assert pick_model("ostatni") == "gpt-4o-mini"

    def test_escalation_list_matches_skill(self):
        assert {"danove", "cizinecka", "soudni"} == ESCALATION_CATEGORIES


class TestBuildSystemPrompt:
    def test_contains_language(self):
        prompt = _build_system_prompt("danove", "ru", False)
        assert "ЯЗЫК ОТВЕТА: ru" in prompt

    def test_contains_datova_schranka_yes(self):
        prompt = _build_system_prompt("ucty", "cs", True)
        assert "да" in prompt

    def test_contains_datova_schranka_no(self):
        prompt = _build_system_prompt("ucty", "cs", False)
        assert "нет" in prompt

    def test_contains_category_module(self):
        prompt = _build_system_prompt("danove", "cs", False)
        assert "Finanční úřad" in prompt
        assert "Platební výměr" in prompt

    def test_fallback_category(self):
        prompt = _build_system_prompt("unknown_category", "cs", False)
        assert "unknown_category" in prompt


class TestAnalyzeDocument:
    @pytest.mark.asyncio
    async def test_happy_path(self, openai_chat_mock):
        valid_response = {
            "document_type": "platebni_vymer",
            "document_type_cz": "Platební výměr",
            "document_type_localized": None,
            "sender": "FÚ",
            "summary": "summary",
            "translation": None,
            "deadlines": [],
            "actions_required": ["pay"],
            "penalty": None,
            "where_to_go": None,
            "documents_needed": [],
            "confidence": 0.9,
            "tips": None,
        }
        openai_chat_mock(valid_response)

        classifier = ClassifierResult(
            ocr_text="hello",
            category="danove",
            doc_type="platebni_vymer",
            sender="FÚ",
            document_language="cs",
            confidence=0.9,
        )
        result, model = await analyze_document(classifier, [], "cs", False)

        assert result.document_type == "platebni_vymer"
        assert model == "gpt-4o"  # danove → escalation

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="not json"))]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        monkeypatch.setattr("app.services.explain.get_openai_client", lambda: mock_client)

        classifier = ClassifierResult(
            ocr_text="x",
            category="ucty",
            doc_type="faktura",
            document_language="cs",
            confidence=0.8,
        )
        with pytest.raises(ValueError, match="invalid JSON"):
            await analyze_document(classifier, [], "cs", False)

    @pytest.mark.asyncio
    async def test_schema_violation_raises(self, openai_chat_mock):
        # confidence out of range
        openai_chat_mock(
            {
                "document_type": "x",
                "document_type_cz": "X",
                "summary": "s",
                "confidence": 5.0,
            }
        )

        classifier = ClassifierResult(
            ocr_text="x",
            category="ucty",
            doc_type="faktura",
            document_language="cs",
            confidence=0.8,
        )
        with pytest.raises(ValueError, match="schema"):
            await analyze_document(classifier, [], "cs", False)
