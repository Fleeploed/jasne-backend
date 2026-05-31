"""End-to-end router tests for /profile."""

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock


def _seed_profile(fake_supabase, user_id: str, **overrides):
    row = {
        "id": user_id,
        "display_name": None,
        "language": "cs",
        "has_datova_schranka": False,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    row.update(overrides)
    fake_supabase.table("profiles").rows.append(row)
    return row


def _seed_account_data(fake_supabase, user_id: str):
    scan_id = "22222222-2222-2222-2222-222222222222"
    fake_supabase.table("scans").rows.append(
        {
            "id": scan_id,
            "user_id": user_id,
            "image_paths": [f"{user_id}/scan-1.jpg", f"{user_id}/scan-2.jpg"],
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    fake_supabase.table("scan_messages").rows.append(
        {
            "id": "33333333-3333-3333-3333-333333333333",
            "scan_id": scan_id,
            "user_id": user_id,
            "role": "assistant",
            "content": "result",
            "message_type": "result_card",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    fake_supabase.table("deadlines").rows.append(
        {
            "id": "44444444-4444-4444-4444-444444444444",
            "scan_id": scan_id,
            "user_id": user_id,
            "title": "Pay",
            "due_date": "2027-01-01",
        }
    )
    fake_supabase.table("usage_limits").rows.append(
        {
            "user_id": user_id,
            "month": "2026-05",
            "scans_count": 1,
            "questions_per_scan": {scan_id: 1},
        }
    )
    return scan_id


class TestGetProfile:
    def test_returns_existing(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), language="ru")
        r = client.get("/profile", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["language"] == "ru"
        assert r.json()["id"] == str(test_user_id)

    def test_auto_creates_if_missing(self, client, fake_supabase, auth_headers, test_user_id):
        # No seed — defensive creation should kick in
        r = client.get("/profile", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["id"] == str(test_user_id)
        assert r.json()["language"] == "cs"

    def test_requires_auth(self, client):
        r = client.get("/profile")
        # HTTPBearer(auto_error=True) returns 403 in old FastAPI, 401 in newer versions
        assert r.status_code in (401, 403)

    def test_invalid_token(self, client):
        r = client.get("/profile", headers={"Authorization": "Bearer bogus"})
        assert r.status_code == 401


class TestUpdateProfile:
    def test_update_language(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id))
        r = client.put("/profile", headers=auth_headers, json={"language": "uk"})
        assert r.status_code == 200
        assert r.json()["language"] == "uk"

    def test_update_multiple_fields(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id))
        r = client.put(
            "/profile",
            headers=auth_headers,
            json={"language": "en", "has_datova_schranka": True, "display_name": "Alice"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["language"] == "en"
        assert body["has_datova_schranka"] is True
        assert body["display_name"] == "Alice"

    def test_empty_body_rejected(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id))
        r = client.put("/profile", headers=auth_headers, json={})
        assert r.status_code == 422

    def test_invalid_language(self, client, auth_headers):
        r = client.put("/profile", headers=auth_headers, json={"language": "de"})
        assert r.status_code == 422


class TestDeleteProfile:
    def test_delete_account_success(
        self,
        client,
        fake_supabase,
        fake_supabase_admin,
        auth_headers,
        test_user_id,
        monkeypatch,
    ):
        _seed_profile(fake_supabase, str(test_user_id))
        _seed_account_data(fake_supabase, str(test_user_id))
        revenuecat_delete = AsyncMock()
        monkeypatch.setattr("app.routers.profile._delete_revenuecat_subscriber", revenuecat_delete)

        r = client.delete("/profile", headers=auth_headers)

        assert r.status_code == 204
        assert r.content == b""
        revenuecat_delete.assert_awaited_once_with(str(test_user_id))
        assert fake_supabase.storage.removed == [
            {
                "bucket": "documents",
                "paths": [
                    f"{test_user_id}/scan-1.jpg",
                    f"{test_user_id}/scan-2.jpg",
                ],
            }
        ]
        assert fake_supabase.table("deadlines").rows == []
        assert fake_supabase.table("usage_limits").rows == []
        assert fake_supabase.table("scans").rows == []
        assert fake_supabase.table("scan_messages").rows == []
        assert fake_supabase.table("profiles").rows == []
        assert fake_supabase_admin.auth.admin.deleted_user_ids == [str(test_user_id)]

    def test_delete_account_with_reason(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        monkeypatch,
        caplog,
    ):
        _seed_profile(fake_supabase, str(test_user_id))
        revenuecat_delete = AsyncMock()
        monkeypatch.setattr("app.routers.profile._delete_revenuecat_subscriber", revenuecat_delete)
        caplog.set_level(logging.INFO, logger="app.routers.profile")

        r = client.delete("/profile?reason=expensive", headers=auth_headers)

        assert r.status_code == 204
        assert "account_deleted reason=expensive user_prefix=11111111" in caplog.text

    def test_delete_account_auth_user_failure(
        self,
        client,
        fake_supabase,
        fake_supabase_admin,
        auth_headers,
        test_user_id,
        monkeypatch,
    ):
        _seed_profile(fake_supabase, str(test_user_id))
        _seed_account_data(fake_supabase, str(test_user_id))
        revenuecat_delete = AsyncMock()
        monkeypatch.setattr("app.routers.profile._delete_revenuecat_subscriber", revenuecat_delete)
        fake_supabase_admin.auth.admin.delete_user_error = RuntimeError("admin unavailable")

        r = client.delete("/profile", headers=auth_headers)

        assert r.status_code == 500
        assert r.json() == {
            "detail": "Account data deleted but auth user removal failed. Contact support.",
        }

    def test_delete_account_storage_failure(
        self,
        client,
        fake_supabase,
        fake_supabase_admin,
        auth_headers,
        test_user_id,
        monkeypatch,
    ):
        _seed_profile(fake_supabase, str(test_user_id))
        _seed_account_data(fake_supabase, str(test_user_id))
        fake_supabase.storage.remove_error = RuntimeError("storage unavailable")
        revenuecat_delete = AsyncMock()
        monkeypatch.setattr("app.routers.profile._delete_revenuecat_subscriber", revenuecat_delete)

        r = client.delete("/profile", headers=auth_headers)

        assert r.status_code == 204
        assert fake_supabase.storage.removed == []
        assert fake_supabase.table("scans").rows == []
        assert fake_supabase.table("profiles").rows == []
        assert fake_supabase_admin.auth.admin.deleted_user_ids == [str(test_user_id)]

    def test_delete_account_unauthenticated(self, client):
        r = client.delete("/profile")
        assert r.status_code == 401
