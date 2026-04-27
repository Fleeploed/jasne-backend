"""End-to-end router tests for /scans (multi-page)."""

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


def _seed_scan(fake_supabase, user_id: str, **overrides):
    paths = overrides.pop("image_paths", [f"{user_id}/test.jpg"])
    row = {
        "id": str(uuid4()),
        "user_id": user_id,
        "image_paths": paths,
        "page_count": len(paths),
        "ocr_text": "ocr",
        "category": "ucty",
        "doc_type": "faktura",
        "category_confidence": 0.9,
        "result": {
            "document_type": "faktura",
            "document_type_cz": "Faktura",
            "document_type_localized": None,
            "sender": "ČEZ",
            "summary": "pay bill",
            "translation": None,
            "deadlines": [],
            "actions_required": [],
            "penalty": None,
            "where_to_go": None,
            "documents_needed": [],
            "confidence": 0.85,
            "tips": None,
        },
        "language": "cs",
        "model_used": "gpt-4o-mini",
        "created_at": datetime.now(UTC).isoformat(),
    }
    row.update(overrides)
    fake_supabase.table("scans").rows.append(row)
    return row


class TestCreateScan:
    def test_happy_path_single_page(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        mock_openai_classify,
        mock_openai_analyze,
        mock_rag_empty,
    ):
        _seed_profile(fake_supabase, str(test_user_id))
        r = client.post(
            "/scans",
            headers=auth_headers,
            json={"image_paths": [f"{test_user_id}/test.jpg"]},
        )
        assert r.status_code == 201, r.json()
        body = r.json()
        assert body["category"] == "danove"
        assert body["result"]["document_type"] == "platebni_vymer"
        assert body["model_used"] == "gpt-4o"
        assert body["page_count"] == 1
        # Auto-deadline created
        dd = client.get("/deadlines", headers=auth_headers)
        assert len(dd.json()) == 1
        assert dd.json()[0]["deadline_type"] == "payment"
        assert dd.json()[0]["auto_created"] is True

    def test_multi_page_scan(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        mock_openai_classify,
        mock_openai_analyze,
        mock_rag_empty,
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        paths = [f"{test_user_id}/p{i}.jpg" for i in range(3)]
        r = client.post(
            "/scans",
            headers=auth_headers,
            json={"image_paths": paths},
        )
        assert r.status_code == 201, r.json()
        body = r.json()
        assert body["page_count"] == 3
        assert body["image_paths"] == paths

    def test_free_tier_4_pages_rejected(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        mock_openai_classify,
        mock_openai_analyze,
        mock_rag_empty,
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="free")
        paths = [f"{test_user_id}/p{i}.jpg" for i in range(4)]
        r = client.post("/scans", headers=auth_headers, json={"image_paths": paths})
        assert r.status_code == 402  # PaymentRequired
        assert "max 3 pages" in r.json()["detail"]

    def test_free_tier_monthly_quota(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        mock_openai_classify,
        mock_openai_analyze,
        mock_rag_empty,
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="free")
        # First 5 should succeed
        for _ in range(5):
            r = client.post(
                "/scans",
                headers=auth_headers,
                json={"image_paths": [f"{test_user_id}/p.jpg"]},
            )
            assert r.status_code == 201
        # 6th should be blocked
        r = client.post(
            "/scans",
            headers=auth_headers,
            json={"image_paths": [f"{test_user_id}/p.jpg"]},
        )
        assert r.status_code == 402
        assert "5 scans/month" in r.json()["detail"]

    def test_language_override_from_body(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        mock_openai_classify,
        mock_openai_analyze,
        mock_rag_empty,
    ):
        _seed_profile(fake_supabase, str(test_user_id), language="cs")
        r = client.post(
            "/scans",
            headers=auth_headers,
            json={"image_paths": [f"{test_user_id}/x.jpg"], "language": "ru"},
        )
        assert r.status_code == 201
        assert r.json()["language"] == "ru"

    def test_profile_autocreated_if_missing(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        mock_openai_classify,
        mock_openai_analyze,
        mock_rag_empty,
    ):
        # No profile seed — _ensure_profile must create
        r = client.post(
            "/scans",
            headers=auth_headers,
            json={"image_paths": [f"{test_user_id}/x.jpg"]},
        )
        assert r.status_code == 201

    def test_low_ocr_confidence_early_exit(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        monkeypatch,
        mock_rag_empty,
    ):
        """If OCR confidence is below gate, don't call analyze — return 422."""
        _seed_profile(fake_supabase, str(test_user_id))

        from app.models.schemas import ClassifierResult

        async def fake_classify(urls):
            return ClassifierResult(
                ocr_text="",
                category="ostatni",
                doc_type="unknown",
                document_language="cs",
                confidence=0.0,
                page_count=1,
                error="Photo too blurry",
            )

        analyze_called = {"count": 0}

        async def fake_analyze(*a, **kw):
            analyze_called["count"] += 1
            raise AssertionError("should not reach analyze")

        monkeypatch.setattr("app.routers.scan.classify_document", fake_classify)
        monkeypatch.setattr("app.routers.scan.analyze_document", fake_analyze)

        r = client.post(
            "/scans",
            headers=auth_headers,
            json={"image_paths": [f"{test_user_id}/x.jpg"]},
        )
        assert r.status_code == 422
        assert "blurry" in r.json()["detail"].lower() or "document" in r.json()["detail"].lower()
        assert analyze_called["count"] == 0

    def test_empty_image_paths_rejected(self, client, auth_headers):
        r = client.post("/scans", headers=auth_headers, json={"image_paths": []})
        assert r.status_code == 422

    def test_too_many_pages_rejected_at_schema(self, client, auth_headers):
        # Hard cap is 10 pages — schema-level check
        r = client.post(
            "/scans",
            headers=auth_headers,
            json={"image_paths": [f"p{i}.jpg" for i in range(11)]},
        )
        assert r.status_code == 422


class TestListScans:
    def test_empty(self, client, auth_headers):
        r = client.get("/scans", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_own_scans(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_scan(fake_supabase, str(test_user_id))
        _seed_scan(fake_supabase, str(test_user_id))
        r = client.get("/scans", headers=auth_headers)
        assert len(r.json()) == 2
        assert r.json()[0]["confidence"] == 0.85

    def test_isolates_users(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_scan(fake_supabase, str(test_user_id))
        _seed_scan(fake_supabase, "99999999-9999-9999-9999-999999999999")
        r = client.get("/scans", headers=auth_headers)
        assert len(r.json()) == 1

    def test_limit_clamped(self, client, auth_headers):
        r = client.get("/scans?limit=500", headers=auth_headers)
        assert r.status_code == 200


class TestGetScan:
    def test_by_id(self, client, fake_supabase, auth_headers, test_user_id):
        row = _seed_scan(fake_supabase, str(test_user_id))
        r = client.get(f"/scans/{row['id']}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["id"] == row["id"]
        assert r.json()["page_count"] == 1

    def test_not_found(self, client, auth_headers):
        r = client.get(f"/scans/{uuid4()}", headers=auth_headers)
        assert r.status_code == 404

    def test_cant_access_others(self, client, fake_supabase, auth_headers):
        row = _seed_scan(fake_supabase, "99999999-9999-9999-9999-999999999999")
        r = client.get(f"/scans/{row['id']}", headers=auth_headers)
        assert r.status_code == 404


class TestDeleteScan:
    def test_delete(self, client, fake_supabase, auth_headers, test_user_id):
        row = _seed_scan(fake_supabase, str(test_user_id))
        r = client.delete(f"/scans/{row['id']}", headers=auth_headers)
        assert r.status_code == 204
        r2 = client.get(f"/scans/{row['id']}", headers=auth_headers)
        assert r2.status_code == 404


class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["environment"] == "test"
