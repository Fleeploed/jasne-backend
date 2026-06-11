"""End-to-end router tests for /scans/{scan_id}/chat (Pro Q&A)."""

import json
from datetime import UTC, datetime
from uuid import uuid4

from app.routers.chat import _build_chat_rag_query
from app.services.chat import (
    _build_chat_messages,
    _build_knowledge_context_message,
    _build_scan_context_message,
)
from app.services.rag import _extract_keyword_terms


def _seed_profile(fake_supabase, user_id: str, **overrides):
    row = {
        "id": user_id,
        "display_name": None,
        "language": "cs",
        "has_datova_schranka": False,
        "plan": "free",
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    row.update(overrides)
    fake_supabase.table("profiles").rows.append(row)
    return row


def _seed_scan(fake_supabase, user_id: str):
    sid = str(uuid4())
    fake_supabase.table("scans").rows.append(
        {
            "id": sid,
            "user_id": user_id,
            "image_paths": [f"{user_id}/p.jpg"],
            "page_count": 1,
            "ocr_text": "Platební výměr č. 12345 částka 4 320 Kč",
            "category": "danove",
            "doc_type": "platebni_vymer",
            "category_confidence": 0.95,
            "result": {
                "document_type": "platebni_vymer",
                "document_type_cz": "Platební výměr",
                "summary": "Zaplatit 4 320 Kč",
                "deadlines": [],
                "actions_required": ["Zaplatit"],
                "penalty": "Úrok 8%",
                "documents_needed": [],
                "confidence": 0.9,
            },
            "language": "cs",
            "model_used": "gpt-4o",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return sid


def _seed_cizinecka_pickup_scan(fake_supabase, user_id: str, *, language: str = "ru"):
    sid = str(uuid4())
    fake_supabase.table("scans").rows.append(
        {
            "id": sid,
            "user_id": user_id,
            "image_paths": [f"{user_id}/p.jpg"],
            "page_count": 1,
            "ocr_text": (
                "Výzva k převzetí průkazu o povolení k dlouhodobému pobytu. "
                "S sebou je třeba si přinést platný cestovní doklad a uhradit správní poplatek "
                "VE FORMĚ KOLKU ve výši: [ ] bez poplatku (0 Kč) [ ] 2 500 Kč [ ] 300 Kč [ ] 1 000 Kč."
            ),
            "category": "cizinecka",
            "doc_type": "vyzva_k_prevzeti_prukazu",
            "category_confidence": 0.95,
            "result": {
                "document_type": "vyzva_k_prevzeti_prukazu",
                "document_type_cz": "Výzva k převzetí průkazu",
                "summary": "Нужно прийти за картой.",
                "deadlines": [],
                "actions_required": [
                    "Прийти лично на OAMP.",
                    "Взять действующий паспорт.",
                    "Подготовить kolky 2 500 Kč.",
                ],
                "penalty": "Если не прийти в течение 60 дней, řízení bude zastaveno.",
                "documents_needed": ["Действующий паспорт.", "Kolky 2 500 Kč."],
                "confidence": 0.95,
            },
            "language": language,
            "model_used": "gpt-4o",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return sid


class TestListMessages:
    def test_empty_thread(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        sid = _seed_scan(fake_supabase, str(test_user_id))
        r = client.get(f"/scans/{sid}/chat", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_messages(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        sid = _seed_scan(fake_supabase, str(test_user_id))
        fake_supabase.table("scan_messages").rows.append(
            {
                "id": str(uuid4()),
                "scan_id": sid,
                "user_id": str(test_user_id),
                "role": "user",
                "content": "Co znamená VS?",
                "message_type": "text",
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        r = client.get(f"/scans/{sid}/chat", headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["content"] == "Co znamená VS?"

    def test_404_for_unknown_scan(self, client, auth_headers):
        r = client.get(f"/scans/{uuid4()}/chat", headers=auth_headers)
        assert r.status_code == 404

    def test_pair_with_identical_created_at_keeps_insert_order(
        self, client, fake_supabase, auth_headers, test_user_id
    ):
        # A Q/A pair inserted in one statement shares created_at (now() is
        # per-transaction) and ids are random uuids — only `seq` keeps order.
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        sid = _seed_scan(fake_supabase, str(test_user_id))
        ts = datetime.now(UTC).isoformat()
        fake_supabase.table("scan_messages").rows.extend(
            [
                {
                    # id sorts AFTER the assistant's — created_at/id ordering would flip the pair
                    "id": "ffffffff-0000-0000-0000-000000000001",
                    "scan_id": sid,
                    "user_id": str(test_user_id),
                    "role": "user",
                    "content": "Otázka",
                    "message_type": "text",
                    "created_at": ts,
                    "seq": 1,
                },
                {
                    "id": "00000000-0000-0000-0000-000000000002",
                    "scan_id": sid,
                    "user_id": str(test_user_id),
                    "role": "assistant",
                    "content": "Odpověď",
                    "message_type": "text",
                    "created_at": ts,
                    "seq": 2,
                },
            ]
        )
        r = client.get(f"/scans/{sid}/chat", headers=auth_headers)
        assert r.status_code == 200
        assert [m["role"] for m in r.json()] == ["user", "assistant"]


class TestAskQuestion:
    def test_free_user_blocked(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), plan="free")
        sid = _seed_scan(fake_supabase, str(test_user_id))
        r = client.post(f"/scans/{sid}/chat", headers=auth_headers, json={"question": "Co?"})
        assert r.status_code == 402  # PaymentRequired
        assert "chat" in r.json()["detail"].lower()

    def test_pro_user_can_ask(self, client, fake_supabase, auth_headers, test_user_id, monkeypatch):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        sid = _seed_scan(fake_supabase, str(test_user_id))

        # Mock the answer_question service
        async def fake_rag(question, category, preferred_language=None):
            assert preferred_language == "cs"
            return ["Voroněžská 144/20, Liberec\nPo, St: 8.00–16.00\nPá: 8.00–13.00"]

        async def fake_answer(scan_row, history, question, language, rag_chunks=None):
            assert rag_chunks == ["Voroněžská 144/20, Liberec\nPo, St: 8.00–16.00\nPá: 8.00–13.00"]
            return f"Odpověď na: {question}"

        monkeypatch.setattr("app.routers.chat.retrieve_chat_chunks", fake_rag)
        monkeypatch.setattr("app.routers.chat.answer_question", fake_answer)

        r = client.post(
            f"/scans/{sid}/chat",
            headers=auth_headers,
            json={"question": "Co znamená VS?"},
        )
        assert r.status_code == 201, r.json()
        body = r.json()
        assert body["user_message"]["content"] == "Co znamená VS?"
        assert "Odpověď na: Co znamená VS?" in body["assistant_message"]["content"]
        assert body["questions_used"] == 1
        assert body["questions_remaining"] == 9

    def test_uses_scan_language_not_profile_language(
        self, client, fake_supabase, auth_headers, test_user_id, monkeypatch
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro", language="cs")
        sid = _seed_cizinecka_pickup_scan(fake_supabase, str(test_user_id), language="ru")
        seen = {}

        async def fake_answer(scan_row, history, question, language, rag_chunks=None):
            seen["language"] = language
            return "Идите на OAMP Cigánkova."

        monkeypatch.setattr("app.routers.chat.answer_question", fake_answer)

        r = client.post(
            f"/scans/{sid}/chat",
            headers=auth_headers,
            json={"question": "куда идти?"},
        )
        assert r.status_code == 201, r.json()
        assert seen["language"] == "ru"
        assert r.json()["assistant_message"]["content"] == "Идите на OAMP Cigánkova."

    def test_chat_context_suppresses_unconfirmed_fee_guidance(self, fake_supabase, test_user_id):
        sid = _seed_cizinecka_pickup_scan(fake_supabase, str(test_user_id), language="ru")
        scan_row = fake_supabase.table("scans").rows[-1]

        context = _build_scan_context_message(scan_row)
        assert "Подготовить kolky" not in context
        assert "Kolky 2 500" not in context
        assert "POPLATEK/KOLKY" in context

        messages = _build_chat_messages(scan_row, [], "куда идти?", "ru")
        assert "JAZYK ODPOVĚDI: ru" in messages[0]["content"]
        assert "Nikdy neodpovídej česky" in messages[0]["content"]
        assert "что на фото" in messages[0]["content"]
        assert "Neříkej, že neumíš analyzovat fotky" in messages[0]["content"]
        assert "JAZYK SKENU: ru" in messages[1]["content"]
        assert "BÁZE ZNALOSTÍ (RAG)" in messages[2]["content"]
        assert messages[3]["content"] == "HISTORIE CHATU: Zatím žádná předchozí textová historie."

    def test_chat_messages_include_history_marker(self, fake_supabase, test_user_id):
        sid = _seed_cizinecka_pickup_scan(fake_supabase, str(test_user_id), language="ru")
        scan_row = fake_supabase.table("scans").rows[-1]

        messages = _build_chat_messages(
            scan_row,
            [
                {"role": "user", "content": "что на фото?"},
                {"role": "assistant", "content": "На фото výzva k převzetí průkazu."},
            ],
            "а куда идти?",
            "ru",
        )

        assert messages[3]["role"] == "system"
        assert "dosavadní historie tohoto chatu" in messages[3]["content"]
        assert messages[4] == {"role": "user", "content": "что на фото?"}
        assert messages[5] == {
            "role": "assistant",
            "content": "На фото výzva k převzetí průkazu.",
        }
        assert messages[6] == {"role": "user", "content": "а куда идти?"}

    def test_chat_messages_include_rag_context(self, fake_supabase, test_user_id):
        _seed_cizinecka_pickup_scan(fake_supabase, str(test_user_id), language="ru")
        scan_row = fake_supabase.table("scans").rows[-1]

        messages = _build_chat_messages(
            scan_row,
            [],
            "рабочие часы VORONĚŽSKÁ 144/20?",
            "ru",
            ["Voroněžská 144/20, Liberec\nPo, St: 8.00–16.00\nPá: 8.00–13.00"],
        )

        assert "BÁZE ZNALOSTÍ (RAG) PRO AKTUÁLNÍ OTÁZKU" in messages[2]["content"]
        assert "Voroněžská 144/20" in messages[2]["content"]
        assert "Po, St: 8.00–16.00" in messages[2]["content"]

    def test_short_hours_question_adds_scan_address_to_rag_query(self, fake_supabase, test_user_id):
        _seed_cizinecka_pickup_scan(fake_supabase, str(test_user_id), language="ru")
        scan_row = fake_supabase.table("scans").rows[-1]
        scan_row["result"]["where_to_go"] = (
            "Ministerstvo vnitra ČR, Odbor azylové a migrační politiky, Žukovského 888/2, 161 00 Praha"
        )

        query = _build_chat_rag_query("часы работы", scan_row)

        assert "часы работы" in query
        assert "Žukovského 888/2" in query

    def test_empty_rag_context_tells_model_not_to_guess(self):
        context = _build_knowledge_context_message([])
        assert "nebyly nalezeny" in context
        assert "nemáš" in context

    def test_chat_keyword_terms_keep_exact_address_tokens(self):
        terms = _extract_keyword_terms("рабочие часы VORONĚŽSKÁ 144/20, 460 01 LIBEREC?")
        assert "VORONĚŽSKÁ" in terms
        assert "144/20" in terms
        assert "LIBEREC" in terms
        assert "рабочие" not in terms

    def test_pro_user_capped_at_10(self, client, fake_supabase, auth_headers, test_user_id, monkeypatch):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        sid = _seed_scan(fake_supabase, str(test_user_id))

        async def fake_answer(*a, **kw):
            return "ok"

        monkeypatch.setattr("app.routers.chat.answer_question", fake_answer)

        for _ in range(10):
            r = client.post(f"/scans/{sid}/chat", headers=auth_headers, json={"question": "Q"})
            assert r.status_code == 201
        # 11th
        r = client.post(f"/scans/{sid}/chat", headers=auth_headers, json={"question": "Q"})
        assert r.status_code == 402
        assert "10 questions" in r.json()["detail"]

    def test_404_for_unknown_scan(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        r = client.post(
            f"/scans/{uuid4()}/chat",
            headers=auth_headers,
            json={"question": "?"},
        )
        assert r.status_code == 404

    def test_empty_question_rejected(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        sid = _seed_scan(fake_supabase, str(test_user_id))
        r = client.post(f"/scans/{sid}/chat", headers=auth_headers, json={"question": ""})
        assert r.status_code == 422

    def test_persistence_failure_returns_clean_500(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        monkeypatch,
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        sid = _seed_scan(fake_supabase, str(test_user_id))

        async def fake_answer(*a, **kw):
            return "ok"

        class EmptyResponse:
            data = []

        class FailingInsert:
            async def execute(self):
                return EmptyResponse()

        class ScanMessagesTable:
            def insert(self, payload):
                return FailingInsert()

            def select(self, *args, **kwargs):
                return fake_supabase.table("scan_messages").select(*args, **kwargs)

        class DelegatingSupabase:
            def table(self, name):
                if name == "scan_messages":
                    return ScanMessagesTable()
                return fake_supabase.table(name)

        async def fake_get_supabase():
            return DelegatingSupabase()

        monkeypatch.setattr("app.routers.chat.answer_question", fake_answer)
        monkeypatch.setattr("app.routers.chat.get_supabase", fake_get_supabase)

        r = client.post(f"/scans/{sid}/chat", headers=auth_headers, json={"question": "Q"})
        assert r.status_code == 500
        assert r.json()["detail"] == "Failed to persist chat messages"


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
        data = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
        events.append((event, json.loads(data)))
    return events


class TestAskQuestionStream:
    def test_free_user_blocked(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), plan="free")
        sid = _seed_scan(fake_supabase, str(test_user_id))
        r = client.post(f"/scans/{sid}/chat/stream", headers=auth_headers, json={"question": "Co?"})
        assert r.status_code == 402

    def test_pro_user_streams_and_persists(
        self, client, fake_supabase, auth_headers, test_user_id, monkeypatch
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        sid = _seed_scan(fake_supabase, str(test_user_id))

        async def fake_stream(*a, **kw):
            yield "Ahoj"
            yield " světe"

        monkeypatch.setattr("app.routers.chat.stream_answer_chunks", fake_stream)

        r = client.post(
            f"/scans/{sid}/chat/stream",
            headers=auth_headers,
            json={"question": "Co znamená VS?"},
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(r.text)
        assert [name for name, _ in events] == ["token", "token", "done"]
        assert events[0][1] == {"text": "Ahoj"}
        assert events[1][1] == {"text": " světe"}
        done = events[2][1]
        assert done["assistant_message"]["content"] == "Ahoj světe"
        assert done["questions_used"] == 1
        assert done["questions_remaining"] == 9

        messages = fake_supabase.table("scan_messages").rows
        assert messages[-2]["content"] == "Co znamená VS?"
        assert messages[-1]["content"] == "Ahoj světe"

    def test_stream_empty_answer_persists_fallback(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        monkeypatch,
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        sid = _seed_scan(fake_supabase, str(test_user_id))

        async def fake_stream(*a, **kw):
            if False:
                yield "unreachable"

        monkeypatch.setattr("app.routers.chat.stream_answer_chunks", fake_stream)

        r = client.post(f"/scans/{sid}/chat/stream", headers=auth_headers, json={"question": "?"})
        assert r.status_code == 200
        events = _parse_sse(r.text)
        assert [name for name, _ in events] == ["token", "done"]
        assert "Bohužel" in events[0][1]["text"]
        assert "Bohužel" in events[1][1]["assistant_message"]["content"]
