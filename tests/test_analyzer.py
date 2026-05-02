"""Tests for analyzer service: prompt building + analyze_document with mocked OpenAI."""

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.schemas import ClassifierResult
from app.services.analyzer import _build_system_prompt, _extract_completed_json_fields, analyze_document


class TestBuildSystemPrompt:
    def test_contains_language(self):
        prompt = _build_system_prompt("danove", "ru", False)
        assert "JAZYK ODPOVĚDI: ru" in prompt
        assert "ЯЗЫКОВОЙ ПРОМПТ: РУССКИЙ" in prompt
        assert "`actions_required`: на русском" in prompt
        assert "`document_type_cz`: название типа документа на чешском" in prompt

    def test_ukrainian_prompt_is_separate(self):
        prompt = _build_system_prompt("cizinecka", "uk", False)
        assert "МОВНИЙ ПРОМПТ: УКРАЇНСЬКА" in prompt
        assert "`actions_required`: українською" in prompt
        assert "Не відповідай чеською" in prompt

    def test_english_prompt_is_separate(self):
        prompt = _build_system_prompt("cizinecka", "en", False)
        assert "LANGUAGE PROMPT: ENGLISH" in prompt
        assert "`actions_required`: English" in prompt
        assert "Do not answer in Czech" in prompt

    def test_czech_prompt_is_separate(self):
        prompt = _build_system_prompt("cizinecka", "cs", False)
        assert "JAZYKOVÝ PROMPT: ČEŠTINA" in prompt
        assert "`actions_required`: česky" in prompt
        assert "`translation`: null" in prompt

    def test_extracts_completed_stream_fields(self):
        emitted = set()
        buffer = '{"summary":"Hotovo","actions_required":["Zkontrolovat stav řízení"],"penalty":'
        fields = _extract_completed_json_fields(buffer, emitted)
        assert fields == {
            "summary": "Hotovo",
            "actions_required": ["Zkontrolovat stav řízení"],
        }

        emitted.update(fields)
        buffer += '"Pokuta nehrozí","where_to_go":'
        fields = _extract_completed_json_fields(buffer, emitted)
        assert fields == {"penalty": "Pokuta nehrozí"}

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

    def test_cizinecka_prompt_guards_submission_confirmation(self):
        prompt = _build_system_prompt("cizinecka", "cs", False)
        assert "POTVRZENÍ O PODÁNÍ ŽÁDOSTI" in prompt
        assert "`documents_needed`: []" in prompt
        assert "`penalty`: null" in prompt

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
            ocr_text=f"Platební výměr se splatností {future.isoformat()}",
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
            ocr_text="Faktura bez termínu splatnosti",
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
            ocr_text=f"Faktura se splatností {future.isoformat()}",
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
    async def test_submission_confirmation_drops_rag_hallucinated_requirements(self, openai_chat_mock):
        openai_chat_mock(
            {
                "document_type": "potvrzeni_o_podani_zadosti",
                "document_type_cz": "Potvrzení o podání žádosti",
                "document_type_localized": None,
                "sender": "Ministerstvo vnitra ČR",
                "summary": "Byla podána žádost o prodloužení zaměstnanecké karty.",
                "translation": None,
                "actions_required": [
                    "Doložit doklad o ubytování",
                    "Uschovat potvrzení a sledovat stav žádosti",
                ],
                "penalty": "Pokud nebude žádost vyřízena včas, hrozí správní vyhoštění.",
                "where_to_go": "OAMP Praha I",
                "documents_needed": [
                    "Doklad o zajištění ubytování",
                    "Platný cestovní doklad",
                    "Pracovní smlouva",
                ],
                "confidence": 0.95,
                "tips": "Sledujte stav žádosti online.",
                "deadlines": [],
            }
        )
        classifier = ClassifierResult(
            ocr_text=(
                "POTVRZENÍ O PODÁNÍ ŽÁDOSTI. Tímto potvrzujeme, že výše uvedený cizinec "
                "dne 25.03.2026 podal žádost o prodloužení doby platnosti zaměstnanecké karty."
            ),
            category="cizinecka",
            doc_type="potvrzeni_o_podani_zadosti",
            sender="Ministerstvo vnitra ČR",
            document_language="cs",
            confidence=0.95,
            page_count=1,
        )
        result, _ = await analyze_document(
            classifier,
            ["Obecný RAG o přílohách k zaměstnanecké kartě."],
            "cs",
            False,
        )

        assert result.penalty is None
        assert result.documents_needed == []
        assert result.deadlines == []
        assert result.actions_required == ["Uschovat potvrzení a sledovat stav žádosti"]

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
