"""End-to-end router tests for /profile."""

import logging
from datetime import UTC, datetime, timedelta


def _seed_profile(fake_supabase, user_id: str, **overrides):
    row = {
        "id": user_id,
        "display_name": None,
        "language": "cs",
        "has_datova_schranka": False,
        "plan": "free",
        "deletion_status": "active",
        "deletion_requested_at": None,
        "deletion_scheduled_at": None,
        "deletion_reason": None,
        "deletion_restored_at": None,
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

    def test_pending_deletion_rejected(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(
            fake_supabase,
            str(test_user_id),
            deletion_status="pending",
            deletion_scheduled_at="2026-06-11T00:00:00+00:00",
        )

        r = client.put("/profile", headers=auth_headers, json={"language": "uk"})

        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "account_pending_deletion"


class TestDeleteProfile:
    def test_delete_account_schedules_soft_delete(
        self,
        client,
        fake_supabase,
        fake_supabase_admin,
        auth_headers,
        test_user_id,
    ):
        _seed_profile(fake_supabase, str(test_user_id), expo_push_token="ExponentPushToken[test]")
        _seed_account_data(fake_supabase, str(test_user_id))

        r = client.delete("/profile", headers=auth_headers)

        assert r.status_code == 204
        assert r.content == b""
        assert fake_supabase.storage.removed == []
        assert len(fake_supabase.table("deadlines").rows) == 1
        assert len(fake_supabase.table("usage_limits").rows) == 1
        assert len(fake_supabase.table("scans").rows) == 1
        assert len(fake_supabase.table("scan_messages").rows) == 1
        assert fake_supabase_admin.auth.admin.deleted_user_ids == []

        profile = fake_supabase.table("profiles").rows[0]
        assert profile["deletion_status"] == "pending"
        assert profile["deletion_requested_at"] is not None
        assert profile["deletion_scheduled_at"] is not None
        assert profile["expo_push_token"] is None

        requested_at = datetime.fromisoformat(profile["deletion_requested_at"])
        scheduled_at = datetime.fromisoformat(profile["deletion_scheduled_at"])
        assert (scheduled_at - requested_at).days == 7

    def test_delete_account_with_reason(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
        caplog,
    ):
        _seed_profile(fake_supabase, str(test_user_id))
        caplog.set_level(logging.INFO, logger="app.routers.profile")

        r = client.delete("/profile?reason=expensive", headers=auth_headers)

        assert r.status_code == 204
        assert fake_supabase.table("profiles").rows[0]["deletion_reason"] == "expensive"
        assert "account_deletion_scheduled reason=expensive user_prefix=11111111" in caplog.text

    def test_delete_account_is_idempotent_when_already_pending(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
    ):
        _seed_profile(
            fake_supabase,
            str(test_user_id),
            deletion_status="pending",
            deletion_requested_at="2026-06-04T00:00:00+00:00",
            deletion_scheduled_at="2026-06-11T00:00:00+00:00",
        )

        r = client.delete("/profile?reason=other", headers=auth_headers)

        assert r.status_code == 204
        profile = fake_supabase.table("profiles").rows[0]
        assert profile["deletion_reason"] is None
        assert profile["deletion_scheduled_at"] == "2026-06-11T00:00:00+00:00"

    def test_restore_account(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
    ):
        _seed_profile(
            fake_supabase,
            str(test_user_id),
            deletion_status="pending",
            deletion_requested_at="2026-06-04T00:00:00+00:00",
            deletion_scheduled_at="2026-06-11T00:00:00+00:00",
            deletion_reason="not_needed",
        )

        r = client.post("/profile/restore", headers=auth_headers)

        assert r.status_code == 200
        body = r.json()
        assert body["deletion_status"] == "active"
        assert body["deletion_requested_at"] is None
        assert body["deletion_scheduled_at"] is None
        assert body["deletion_reason"] is None
        assert body["deletion_restored_at"] is not None

    def test_pending_deletion_status_available(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
    ):
        _seed_profile(
            fake_supabase,
            str(test_user_id),
            deletion_status="pending",
            deletion_requested_at="2026-06-04T00:00:00+00:00",
            deletion_scheduled_at="2026-06-11T00:00:00+00:00",
            deletion_reason="other",
        )

        r = client.get("/profile/deletion-status", headers=auth_headers)

        assert r.status_code == 200
        assert r.json() == {
            "deletion_status": "pending",
            "deletion_requested_at": "2026-06-04T00:00:00Z",
            "deletion_scheduled_at": "2026-06-11T00:00:00Z",
            "deletion_reason": "other",
        }

    def test_pending_deletion_blocks_normal_api(
        self,
        client,
        fake_supabase,
        auth_headers,
        test_user_id,
    ):
        _seed_profile(
            fake_supabase,
            str(test_user_id),
            deletion_status="pending",
            deletion_requested_at="2026-06-04T00:00:00+00:00",
            deletion_scheduled_at="2026-06-11T00:00:00+00:00",
        )

        r = client.get("/limits", headers=auth_headers)

        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "account_pending_deletion"

    def test_delete_account_unauthenticated(self, client):
        r = client.delete("/profile")
        assert r.status_code == 401


class TestAccountDeletionPurge:
    def test_purges_due_pending_account(
        self,
        client,
        fake_supabase,
        fake_supabase_admin,
        test_user_id,
        monkeypatch,
    ):
        monkeypatch.setattr("app.routers.internal._verify_cron_secret", lambda value: None)
        _seed_profile(
            fake_supabase,
            str(test_user_id),
            deletion_status="pending",
            deletion_requested_at="2026-06-01T00:00:00+00:00",
            deletion_scheduled_at="2026-06-02T00:00:00+00:00",
        )
        _seed_account_data(fake_supabase, str(test_user_id))
        fake_supabase.table("push_tokens").rows.append(
            {
                "id": "55555555-5555-5555-5555-555555555555",
                "user_id": str(test_user_id),
                "expo_push_token": "ExponentPushToken[test]",
                "enabled": True,
            }
        )

        r = client.post("/internal/accounts/purge-deleted")

        assert r.status_code == 200
        assert r.json() == {"scanned_profiles": 1, "purged": 1, "failed": 0}
        assert fake_supabase.storage.removed == [
            {
                "bucket": "documents",
                "paths": [
                    f"{test_user_id}/scan-1.jpg",
                    f"{test_user_id}/scan-2.jpg",
                ],
            }
        ]
        assert fake_supabase.table("push_tokens").rows == []
        assert fake_supabase.table("deadlines").rows == []
        assert fake_supabase.table("usage_limits").rows == []
        assert fake_supabase.table("scans").rows == []
        assert fake_supabase.table("scan_messages").rows == []
        assert fake_supabase.table("profiles").rows == []
        assert fake_supabase_admin.auth.admin.deleted_user_ids == [str(test_user_id)]

    def test_keeps_pending_account_until_scheduled_date(
        self,
        client,
        fake_supabase,
        fake_supabase_admin,
        test_user_id,
        monkeypatch,
    ):
        monkeypatch.setattr("app.routers.internal._verify_cron_secret", lambda value: None)
        future = datetime.now(UTC) + timedelta(days=1)
        _seed_profile(
            fake_supabase,
            str(test_user_id),
            deletion_status="pending",
            deletion_requested_at=datetime.now(UTC).isoformat(),
            deletion_scheduled_at=future.isoformat(),
        )
        _seed_account_data(fake_supabase, str(test_user_id))

        r = client.post("/internal/accounts/purge-deleted")

        assert r.status_code == 200
        assert r.json() == {"scanned_profiles": 0, "purged": 0, "failed": 0}
        assert len(fake_supabase.table("profiles").rows) == 1
        assert len(fake_supabase.table("scans").rows) == 1
        assert fake_supabase_admin.auth.admin.deleted_user_ids == []
