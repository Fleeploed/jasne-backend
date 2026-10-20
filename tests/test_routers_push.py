from datetime import UTC, datetime
from uuid import uuid4


def _valid_payload(**overrides):
    payload = {
        "expo_push_token": "ExponentPushToken[test-token]",
        "platform": "ios",
        "timezone": "Europe/Prague",
        "locale": "ru",
        "app_version": "0.1.0",
    }
    payload.update(overrides)
    return payload


class TestRegisterPushToken:
    def test_registers_token(self, client, fake_supabase, auth_headers, test_user_id):
        r = client.post("/push-tokens", headers=auth_headers, json=_valid_payload())

        assert r.status_code == 201
        body = r.json()
        assert body["user_id"] == str(test_user_id)
        assert body["expo_push_token"] == "ExponentPushToken[test-token]"
        assert body["enabled"] is True
        assert len(fake_supabase.table("push_tokens").rows) == 1

    def test_upsert_keeps_single_row(self, client, fake_supabase, auth_headers):
        r1 = client.post("/push-tokens", headers=auth_headers, json=_valid_payload(locale="ru"))
        r2 = client.post("/push-tokens", headers=auth_headers, json=_valid_payload(locale="cs"))

        assert r1.status_code == 201
        assert r2.status_code == 201
        rows = fake_supabase.table("push_tokens").rows
        assert len(rows) == 1
        assert rows[0]["locale"] == "cs"

    def test_rejects_non_expo_token(self, client, auth_headers):
        r = client.post(
            "/push-tokens",
            headers=auth_headers,
            json=_valid_payload(expo_push_token="native-token"),
        )

        assert r.status_code == 422

    def test_requires_auth(self, client):
        r = client.post("/push-tokens", json=_valid_payload())
        assert r.status_code in (401, 403)


class TestDisablePushToken:
    def test_disables_own_token(self, client, fake_supabase, auth_headers, test_user_id):
        token_id = str(uuid4())
        fake_supabase.table("push_tokens").rows.append(
            {
                "id": token_id,
                "user_id": str(test_user_id),
                "expo_push_token": "ExponentPushToken[test-token]",
                "platform": "ios",
                "timezone": "Europe/Prague",
                "locale": "ru",
                "enabled": True,
                "created_at": datetime.now(UTC).isoformat(),
                "last_seen_at": datetime.now(UTC).isoformat(),
            }
        )

        r = client.delete(f"/push-tokens/{token_id}", headers=auth_headers)

        assert r.status_code == 204
        assert fake_supabase.table("push_tokens").rows[0]["enabled"] is False
