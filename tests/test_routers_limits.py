"""End-to-end router tests for /limits."""

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


class TestGetLimits:
    def test_free_user_defaults(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), plan="free")
        r = client.get("/limits", headers=auth_headers)
        assert r.status_code == 200
        body = r.json()
        assert body["plan"] == "free"
        assert body["scans_used"] == 0
        assert body["scans_max"] == 5
        assert body["scans_remaining"] == 5
        assert body["max_pages_per_scan"] == 3
        assert body["max_questions_per_scan"] == 0
        assert body["questions_used_for_scan"] is None  # no scan_id in query

    def test_pro_user_caps(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        r = client.get("/limits", headers=auth_headers)
        body = r.json()
        assert body["scans_max"] == 100
        assert body["max_pages_per_scan"] == 10
        assert body["max_questions_per_scan"] == 10

    def test_pro_user_with_expiration_uses_subscription_period(
        self, client, fake_supabase, auth_headers, test_user_id
    ):
        _seed_profile(
            fake_supabase,
            str(test_user_id),
            plan="pro",
            subscription_product_id="jasne_pro_monthly",
            subscription_expires_at="2099-06-22T10:30:00Z",
        )
        r = client.get("/limits", headers=auth_headers)
        body = r.json()
        assert body["month"] == "pro:2099-05-22:2099-06-22"

    def test_with_scan_id_returns_chat_count(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        scan_id = uuid4()
        r = client.get(f"/limits?scan_id={scan_id}", headers=auth_headers)
        body = r.json()
        # No questions asked yet
        assert body["questions_used_for_scan"] == 0

    def test_unauthenticated(self, client):
        r = client.get("/limits")
        assert r.status_code in (401, 403)
