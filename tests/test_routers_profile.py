"""End-to-end router tests for /profile."""

from datetime import UTC, datetime


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
