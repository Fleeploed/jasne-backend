"""End-to-end router tests for /scans/{scan_id}/chat (Pro Q&A)."""

from datetime import UTC, datetime
from uuid import uuid4


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
        async def fake_answer(scan_row, history, question, language):
            return f"Odpověď na: {question}"

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
