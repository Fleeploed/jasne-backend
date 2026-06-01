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
            category="danove",  # → escalation
            doc_type="platebni_vymer",
            sender="FÚ",
            confidence=0.9,
        )
        result, model = await analyze_document(
            classifier,
            f"Platební výměr se splatností {future.isoformat()}",
            1,
            [],
            "cs",
            False,
        )

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
            category="ucty",  # → default model
            doc_type="faktura",
            confidence=0.8,
        )
        _, model = await analyze_document(
            classifier, "Faktura bez termínu splatnosti", 1, [], "cs", False
        )
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
            category="ucty",
            doc_type="faktura",
            confidence=0.9,
        )
        result, _ = await analyze_document(
            classifier, f"Faktura se splatností {future.isoformat()}", 1, [], "cs", False
        )
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
            category="cizinecka",
            doc_type="potvrzeni_o_podani_zadosti",
            sender="Ministerstvo vnitra ČR",
            confidence=0.95,
        )
        ocr_text = (
            "POTVRZENÍ O PODÁNÍ ŽÁDOSTI. Tímto potvrzujeme, že výše uvedený cizinec "
            "dne 25.03.2026 podal žádost o prodloužení doby platnosti zaměstnanecké karty."
        )
        result, _ = await analyze_document(
            classifier,
            ocr_text,
            1,
            ["Obecný RAG o přílohách k zaměstnanecké kartě."],
            "cs",
            False,
        )

        assert result.penalty is None
        assert result.documents_needed == []
        assert result.deadlines == []
        assert result.actions_required == ["Uschovat potvrzení a sledovat stav žádosti"]

    @pytest.mark.asyncio
    async def test_card_pickup_notice_suppresses_unconfirmed_fee_guidance(self, openai_chat_mock):
        openai_chat_mock(
            {
                "document_type": "vyzva_k_prevzeti_prukazu",
                "document_type_cz": "Výzva k převzetí průkazu",
                "document_type_localized": None,
                "sender": "Ministerstvo vnitra ČR",
                "summary": (
                    "Ministerstvo vnitra vyzývá k převzetí průkazu dne 12.01.2023. "
                    "Je nutné přinést platný cestovní doklad a uhradit správní poplatek ve formě kolku."
                ),
                "translation": None,
                "actions_required": [
                    "Dostavit se osobně dne 12.01.2023 v 14:40.",
                    "Přinést platný cestovní doklad.",
                    "Uhradit správní poplatek ve formě kolku.",
                ],
                "penalty": "Pokud se nedostavíte do 60 dnů, řízení bude zastaveno.",
                "where_to_go": "OAMP Cigánkova 1861/2, Praha 4.",
                "documents_needed": [
                    "Platný cestovní doklad.",
                    "Kolky na správní poplatek.",
                ],
                "confidence": 0.95,
                "tips": "Doporučuji si připravit kolky předem.",
                "deadlines": [],
            }
        )
        classifier = ClassifierResult(
            category="cizinecka",
            doc_type="vyzva_k_prevzeti_prukazu",
            sender="Ministerstvo vnitra ČR",
            confidence=0.95,
        )
        ocr_text = (
            "Výzva k převzetí průkazu o povolení k dlouhodobému pobytu. "
            "S sebou je třeba si přinést platný cestovní doklad a uhradit správní poplatek "
            "VE FORMĚ KOLKU ve výši: [ ] bez poplatku (0 Kč) [ ] 2 500 Kč [ ] 300 Kč [ ] 1 000 Kč."
        )
        result, _ = await analyze_document(classifier, ocr_text, 1, [], "cs", False)

        assert "poplatek" not in result.summary.lower()
        assert "kolk" not in result.summary.lower()
        assert result.actions_required == [
            "Dostavit se osobně dne 12.01.2023 v 14:40.",
            "Přinést platný cestovní doklad.",
        ]
        assert result.documents_needed == ["Platný cestovní doklad."]
        assert result.tips is None
        assert result.penalty == "Pokud se nedostavíte do 60 dnů, řízení bude zastaveno."

    @pytest.mark.asyncio
    async def test_card_pickup_notice_keeps_explicit_checked_positive_fee(self, openai_chat_mock):
        openai_chat_mock(
            {
                "document_type": "vyzva_k_prevzeti_prukazu",
                "document_type_cz": "Výzva k převzetí průkazu",
                "summary": "Převzetí průkazu.",
                "deadlines": [],
                "actions_required": ["Uhradit správní poplatek 300 Kč ve formě kolku."],
                "documents_needed": ["Platný cestovní doklad.", "Kolek 300 Kč."],
                "confidence": 0.95,
                "tips": "Připravte si kolek 300 Kč.",
            }
        )
        classifier = ClassifierResult(
            category="cizinecka",
            doc_type="vyzva_k_prevzeti_prukazu",
            sender="Ministerstvo vnitra ČR",
            confidence=0.95,
        )
        ocr_text = (
            "Výzva k převzetí průkazu. Poplatek ve formě kolku ve výši: "
            "[ ] bez poplatku (0 Kč) [ ] 2 500 Kč [x] 300 Kč [ ] 1 000 Kč."
        )
        result, _ = await analyze_document(classifier, ocr_text, 1, [], "cs", False)

        assert result.actions_required == ["Uhradit správní poplatek 300 Kč ve formě kolku."]
        assert "Kolek 300 Kč." in result.documents_needed
        assert result.tips == "Připravte si kolek 300 Kč."

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self, monkeypatch):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="not json"))]
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        monkeypatch.setattr("app.services.analyzer.get_openai_client", lambda: mock_client)

        classifier = ClassifierResult(
            category="ucty",
            doc_type="faktura",
            confidence=0.8,
        )
        with pytest.raises(ValueError, match="invalid JSON"):
            await analyze_document(classifier, "x", 1, [], "cs", False)

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
            category="ucty",
            doc_type="faktura",
            confidence=0.8,
        )
        with pytest.raises(ValueError, match="schema"):
            await analyze_document(classifier, "x", 1, [], "cs", False)
