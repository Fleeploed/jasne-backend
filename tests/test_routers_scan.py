"""End-to-end router tests for /scans (multi-page)."""

import json
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


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
        data = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
        events.append((event, json.loads(data)))
    return events


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

    def test_pdf_scan_is_rendered_before_classifier(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        mock_openai_analyze,
        mock_rag_empty,
        monkeypatch,
    ):
        _seed_profile(fake_supabase, str(test_user_id))

        from app.models.schemas import ClassifierResult, OcrResult

        seen = {}

        async def fake_download(url: str, path: str) -> bytes:
            seen["download_url"] = url
            seen["download_path"] = path
            return b"%PDF-1.6"

        def fake_render(pdf_bytes: bytes, path: str) -> list[str]:
            seen["pdf_bytes"] = pdf_bytes
            seen["render_path"] = path
            return ["data:image/png;base64,page1", "data:image/png;base64,page2"]

        async def fake_extract(image_urls: list[str]) -> OcrResult:
            seen["ocr_urls"] = image_urls
            return OcrResult(ocr_text="PDF text", page_count=len(image_urls), document_language="cs")

        async def fake_classify(ocr_text: str, page_count: int, document_language: str) -> ClassifierResult:
            seen["classifier_ocr"] = ocr_text
            return ClassifierResult(
                category="ostatni",
                doc_type="pdf_document",
                sender=None,
                confidence=0.95,
            )

        monkeypatch.setattr("app.routers.scan._download_signed_object", fake_download)
        monkeypatch.setattr("app.routers.scan._render_pdf_to_png_data_urls", fake_render)
        monkeypatch.setattr("app.routers.scan.extract_text", fake_extract)
        monkeypatch.setattr("app.routers.scan.classify_text", fake_classify)

        path = f"{test_user_id}/document.pdf"
        r = client.post(
            "/scans",
            headers=auth_headers,
            json={"image_paths": [path]},
        )

        assert r.status_code == 201, r.json()
        assert seen["download_url"].startswith(f"https://fake-storage/{path}")
        assert seen["download_path"] == path
        assert seen["pdf_bytes"] == b"%PDF-1.6"
        assert seen["render_path"] == path
        assert seen["ocr_urls"] == ["data:image/png;base64,page1", "data:image/png;base64,page2"]
        assert seen["classifier_ocr"] == "PDF text"
        assert r.json()["page_count"] == 2

    def test_multi_page_pdf_counts_pages_for_quota(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        mock_openai_analyze,
        mock_rag_empty,
        monkeypatch,
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="free")

        async def fake_download(url: str, path: str) -> bytes:
            return b"%PDF-1.6"

        def fake_render(pdf_bytes: bytes, path: str) -> list[str]:
            return [f"data:image/png;base64,page{i}" for i in range(4)]

        async def fake_extract(image_urls: list[str]):
            raise AssertionError("quota should block before OCR")

        async def fake_classify(ocr_text: str, page_count: int, document_language: str):
            raise AssertionError("quota should block before classifier")

        monkeypatch.setattr("app.routers.scan._download_signed_object", fake_download)
        monkeypatch.setattr("app.routers.scan._render_pdf_to_png_data_urls", fake_render)
        monkeypatch.setattr("app.routers.scan.extract_text", fake_extract)
        monkeypatch.setattr("app.routers.scan.classify_text", fake_classify)

        r = client.post(
            "/scans",
            headers=auth_headers,
            json={"image_paths": [f"{test_user_id}/large.pdf"]},
        )

        assert r.status_code == 402
        assert "max 3 pages" in r.json()["detail"]

    def test_rejects_storage_path_outside_user_prefix(
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
            json={"image_paths": ["99999999-9999-9999-9999-999999999999/doc.jpg"]},
        )

        assert r.status_code == 403
        assert "authenticated user" in r.json()["detail"]

    def test_unsupported_file_type_rejected(
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
            json={"image_paths": [f"{test_user_id}/doc.heic"]},
        )

        assert r.status_code == 415
        assert "Unsupported file type" in r.json()["detail"]

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
        """If OCR returns empty text, don't call classify/analyze — return 422."""
        _seed_profile(fake_supabase, str(test_user_id))

        from app.models.schemas import OcrResult

        async def fake_extract(urls):
            return OcrResult(ocr_text="", page_count=1, document_language="cs")

        classify_called = {"count": 0}
        analyze_called = {"count": 0}

        async def fake_classify(*a, **kw):
            classify_called["count"] += 1
            raise AssertionError("should not reach classifier")

        async def fake_analyze(*a, **kw):
            analyze_called["count"] += 1
            raise AssertionError("should not reach analyze")

        monkeypatch.setattr("app.routers.scan.extract_text", fake_extract)
        monkeypatch.setattr("app.routers.scan.classify_text", fake_classify)
        monkeypatch.setattr("app.routers.scan.analyze_document", fake_analyze)

        r = client.post(
            "/scans",
            headers=auth_headers,
            json={"image_paths": [f"{test_user_id}/x.jpg"]},
        )
        assert r.status_code == 422
        assert "document" in r.json()["detail"].lower()
        assert classify_called["count"] == 0
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


class TestCreateScanStream:
    def test_streams_partial_fields_and_persists_scan(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        mock_openai_classify,
        mock_openai_analyze,
        mock_rag_empty,
        monkeypatch,
    ):
        _seed_profile(fake_supabase, str(test_user_id))

        async def fake_stream_analyze_document(*args, **kwargs):
            yield {"type": "field", "field": "summary", "value": mock_openai_analyze.summary}
            yield {
                "type": "field",
                "field": "actions_required",
                "value": mock_openai_analyze.actions_required,
            }
            yield {"type": "field", "field": "penalty", "value": mock_openai_analyze.penalty}
            yield {"type": "done", "analysis": mock_openai_analyze, "model_used": "gpt-4o"}

        monkeypatch.setattr("app.routers.scan.stream_analyze_document", fake_stream_analyze_document)

        r = client.post(
            "/scans/stream",
            headers=auth_headers,
            json={"image_paths": [f"{test_user_id}/test.jpg"]},
        )

        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(r.text)
        names = [name for name, _ in events]
        assert names[:4] == ["status", "status", "status", "status"]
        assert ("field", {"field": "summary", "value": mock_openai_analyze.summary}) in events
        assert (
            "field",
            {"field": "actions_required", "value": mock_openai_analyze.actions_required},
        ) in events
        assert events[-1][0] == "done"
        assert events[-1][1]["result"]["summary"] == mock_openai_analyze.summary
        assert events[-1][1]["model_used"] == "gpt-4o"
        assert len(fake_supabase.table("scans").rows) == 1
        assert len(fake_supabase.table("scan_messages").rows) == 2

    def test_free_tier_4_pages_rejected_before_stream(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        mock_openai_classify,
        mock_rag_empty,
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="free")
        paths = [f"{test_user_id}/p{i}.jpg" for i in range(4)]
        r = client.post("/scans/stream", headers=auth_headers, json={"image_paths": paths})
        assert r.status_code == 402


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


class TestGetScanImageUrls:
    def test_returns_signed_image_urls(self, client, fake_supabase, auth_headers, test_user_id):
        row = _seed_scan(
            fake_supabase,
            str(test_user_id),
            image_paths=[f"{test_user_id}/p1.jpg", f"{test_user_id}/p2.png"],
        )

        r = client.get(f"/scans/{row['id']}/image-urls", headers=auth_headers)

        assert r.status_code == 200
        assert r.json()["urls"] == [
            f"https://fake-storage/{test_user_id}/p1.jpg?ttl=300",
            f"https://fake-storage/{test_user_id}/p2.png?ttl=300",
        ]

    def test_returns_signed_pdf_urls(self, client, fake_supabase, auth_headers, test_user_id):
        row = _seed_scan(
            fake_supabase,
            str(test_user_id),
            image_paths=[f"{test_user_id}/doc.pdf", f"{test_user_id}/page.jpg"],
        )

        r = client.get(f"/scans/{row['id']}/image-urls", headers=auth_headers)

        assert r.status_code == 200
        assert r.json()["urls"] == [
            f"https://fake-storage/{test_user_id}/doc.pdf?ttl=300",
            f"https://fake-storage/{test_user_id}/page.jpg?ttl=300",
        ]

    def test_can_render_pdf_previews(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        monkeypatch,
    ):
        row = _seed_scan(fake_supabase, str(test_user_id), image_paths=[f"{test_user_id}/doc.pdf"])

        async def fake_download(url: str, path: str) -> bytes:
            assert url == f"https://fake-storage/{test_user_id}/doc.pdf?ttl=300"
            assert path == f"{test_user_id}/doc.pdf"
            return b"%PDF"

        def fake_render(pdf_bytes: bytes, path: str) -> list[str]:
            assert pdf_bytes == b"%PDF"
            assert path == f"{test_user_id}/doc.pdf"
            return ["data:image/png;base64,page1"]

        monkeypatch.setattr("app.routers.scan._download_signed_object", fake_download)
        monkeypatch.setattr("app.routers.scan._render_pdf_to_png_data_urls", fake_render)

        r = client.get(f"/scans/{row['id']}/image-urls?render_pdf=true", headers=auth_headers)

        assert r.status_code == 200
        assert r.json()["urls"] == ["data:image/png;base64,page1"]

    def test_cant_access_others(self, client, fake_supabase, auth_headers):
        row = _seed_scan(fake_supabase, "99999999-9999-9999-9999-999999999999")
        r = client.get(f"/scans/{row['id']}/image-urls", headers=auth_headers)
        assert r.status_code == 404


class TestDeleteScan:
    def test_delete(self, client, fake_supabase, auth_headers, test_user_id):
        row = _seed_scan(fake_supabase, str(test_user_id))
        r = client.delete(f"/scans/{row['id']}", headers=auth_headers)
        assert r.status_code == 204
        r2 = client.get(f"/scans/{row['id']}", headers=auth_headers)
        assert r2.status_code == 404

    def test_delete_cascades_messages_and_deadlines(self, client, fake_supabase, auth_headers, test_user_id):
        row = _seed_scan(fake_supabase, str(test_user_id))
        scan_id = row["id"]
        other = _seed_scan(fake_supabase, str(test_user_id))
        fake_supabase.table("scan_messages").rows.extend(
            [
                {
                    "id": str(uuid4()),
                    "scan_id": scan_id,
                    "user_id": str(test_user_id),
                    "role": "user",
                    "content": "x",
                    "message_type": "text",
                    "created_at": datetime.now(UTC).isoformat(),
                },
                {
                    "id": str(uuid4()),
                    "scan_id": other["id"],
                    "user_id": str(test_user_id),
                    "role": "user",
                    "content": "y",
                    "message_type": "text",
                    "created_at": datetime.now(UTC).isoformat(),
                },
            ]
        )
        fake_supabase.table("deadlines").rows.extend(
            [
                {
                    "id": str(uuid4()),
                    "user_id": str(test_user_id),
                    "scan_id": scan_id,
                    "title": "t",
                    "description": None,
                    "due_date": "2099-01-01",
                    "reminder_days": [1],
                    "deadline_type": "action",
                    "status": "active",
                    "auto_created": True,
                    "created_at": datetime.now(UTC).isoformat(),
                },
                {
                    "id": str(uuid4()),
                    "user_id": str(test_user_id),
                    "scan_id": other["id"],
                    "title": "keep",
                    "description": None,
                    "due_date": "2099-01-01",
                    "reminder_days": [1],
                    "deadline_type": "action",
                    "status": "active",
                    "auto_created": True,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            ]
        )

        r = client.delete(f"/scans/{scan_id}", headers=auth_headers)
        assert r.status_code == 204

        msgs = fake_supabase.table("scan_messages").rows
        assert all(m["scan_id"] != scan_id for m in msgs)
        assert any(m["scan_id"] == other["id"] for m in msgs)
        dds = fake_supabase.table("deadlines").rows
        assert all(d["scan_id"] != scan_id for d in dds)
        assert any(d["scan_id"] == other["id"] for d in dds)

    def test_delete_404_when_not_owner(self, client, fake_supabase, auth_headers):
        row = _seed_scan(fake_supabase, "99999999-9999-9999-9999-999999999999")
        r = client.delete(f"/scans/{row['id']}", headers=auth_headers)
        assert r.status_code == 404


class TestBulkDeleteScans:
    def test_deletes_all_user_data_and_returns_counts(
        self, client, fake_supabase, auth_headers, test_user_id
    ):
        s1 = _seed_scan(fake_supabase, str(test_user_id))
        s2 = _seed_scan(fake_supabase, str(test_user_id))
        fake_supabase.table("scan_messages").rows.append(
            {
                "id": str(uuid4()),
                "scan_id": s1["id"],
                "user_id": str(test_user_id),
                "role": "user",
                "content": "q",
                "message_type": "text",
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        fake_supabase.table("deadlines").rows.extend(
            [
                {
                    "id": str(uuid4()),
                    "user_id": str(test_user_id),
                    "scan_id": s1["id"],
                    "title": "a",
                    "description": None,
                    "due_date": "2099-01-01",
                    "reminder_days": [1],
                    "deadline_type": "action",
                    "status": "active",
                    "auto_created": True,
                    "created_at": datetime.now(UTC).isoformat(),
                },
                {
                    "id": str(uuid4()),
                    "user_id": str(test_user_id),
                    "scan_id": s2["id"],
                    "title": "b",
                    "description": None,
                    "due_date": "2099-01-01",
                    "reminder_days": [1],
                    "deadline_type": "action",
                    "status": "active",
                    "auto_created": True,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            ]
        )
        # Seed usage row for current month — must remain untouched after delete.
        from app.services.limits import current_month

        fake_supabase.table("usage_limits").rows.append(
            {
                "user_id": str(test_user_id),
                "month": current_month(),
                "scans_count": 4,
                "questions_per_scan": {s1["id"]: 2},
            }
        )

        r = client.delete("/scans", headers=auth_headers)
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body == {"deleted_scans": 2, "deleted_deadlines": 2}

        assert fake_supabase.table("scans").rows == []
        assert fake_supabase.table("scan_messages").rows == []
        assert fake_supabase.table("deadlines").rows == []

        # Monthly counter is preserved: deletion does not refund quota.
        usage = fake_supabase.table("usage_limits").rows[0]
        assert usage["scans_count"] == 4
        assert usage["questions_per_scan"] == {s1["id"]: 2}

    def test_does_not_touch_other_users(self, client, fake_supabase, auth_headers, test_user_id):
        other_id = "99999999-9999-9999-9999-999999999999"
        _seed_scan(fake_supabase, str(test_user_id))
        other_scan = _seed_scan(fake_supabase, other_id)
        fake_supabase.table("deadlines").rows.append(
            {
                "id": str(uuid4()),
                "user_id": other_id,
                "scan_id": other_scan["id"],
                "title": "other",
                "description": None,
                "due_date": "2099-01-01",
                "reminder_days": [1],
                "deadline_type": "action",
                "status": "active",
                "auto_created": True,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

        r = client.delete("/scans", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == {"deleted_scans": 1, "deleted_deadlines": 0}

        scans_left = fake_supabase.table("scans").rows
        assert len(scans_left) == 1 and scans_left[0]["user_id"] == other_id
        dds = fake_supabase.table("deadlines").rows
        assert len(dds) == 1 and dds[0]["user_id"] == other_id

    def test_empty_account_returns_zero_counts(self, client, auth_headers):
        r = client.delete("/scans", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == {"deleted_scans": 0, "deleted_deadlines": 0}


class TestDeleteDoesNotResetUsage:
    """Regression: deletion must never refund the monthly scan quota."""

    def test_single_scan_delete_does_not_change_scans_count(
        self, client, fake_supabase, auth_headers, test_user_id
    ):
        from app.services.limits import current_month

        row = _seed_scan(fake_supabase, str(test_user_id))
        fake_supabase.table("usage_limits").rows.append(
            {
                "user_id": str(test_user_id),
                "month": current_month(),
                "scans_count": 3,
                "questions_per_scan": {row["id"]: 1},
            }
        )

        r = client.delete(f"/scans/{row['id']}", headers=auth_headers)
        assert r.status_code == 204

        usage = fake_supabase.table("usage_limits").rows[0]
        assert usage["scans_count"] == 3
        assert usage["questions_per_scan"] == {row["id"]: 1}

    def test_bulk_delete_does_not_change_scans_count(self, client, fake_supabase, auth_headers, test_user_id):
        from app.services.limits import current_month

        _seed_scan(fake_supabase, str(test_user_id))
        _seed_scan(fake_supabase, str(test_user_id))
        fake_supabase.table("usage_limits").rows.append(
            {
                "user_id": str(test_user_id),
                "month": current_month(),
                "scans_count": 5,
                "questions_per_scan": {},
            }
        )

        r = client.delete("/scans", headers=auth_headers)
        assert r.status_code == 200

        usage = fake_supabase.table("usage_limits").rows[0]
        assert usage["scans_count"] == 5

    def test_free_user_cannot_bypass_quota_by_deleting(
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

        # Exhaust the Free monthly cap (5 scans).
        for _ in range(5):
            r = client.post(
                "/scans",
                headers=auth_headers,
                json={"image_paths": [f"{test_user_id}/p.jpg"]},
            )
            assert r.status_code == 201

        # Wipe everything — counter must NOT reset.
        r = client.delete("/scans", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["deleted_scans"] == 5

        # 6th attempt must still be blocked by the monthly cap.
        r = client.post(
            "/scans",
            headers=auth_headers,
            json={"image_paths": [f"{test_user_id}/p.jpg"]},
        )
        assert r.status_code == 402
        assert "5 scans/month" in r.json()["detail"]


class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["environment"] == "test"
