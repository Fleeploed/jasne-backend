"""RevenueCat webhook and subscription sync tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.services.revenuecat import plan_from_subscriber, plan_from_webhook_event


def _settings(**overrides):
    values = {
        "openai_api_key": "test-key",
        "supabase_url": "https://test.supabase.co",
        "supabase_service_key": "test-service-key",
        "supabase_jwt_secret": "test-jwt-secret",
        "environment": "test",
        "revenuecat_webhook_auth": "Bearer webhook-secret",
        "revenuecat_api_key": "rc-secret",
        "revenuecat_entitlement_id": "pro",
    }
    values.update(overrides)
    return Settings(**values)


def _seed_profile(fake_supabase, user_id: str, plan: str = "free"):
    fake_supabase.table("profiles").rows.append(
        {
            "id": user_id,
            "display_name": None,
            "language": "cs",
            "has_datova_schranka": False,
            "plan": plan,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )


def _event(user_id: str, event_type: str = "INITIAL_PURCHASE") -> dict:
    return {
        "api_version": "1.0",
        "event": {
            "id": "event-1",
            "type": event_type,
            "app_user_id": user_id,
            "entitlement_ids": ["pro"],
            "expiration_at_ms": int((datetime.now(UTC) + timedelta(days=30)).timestamp() * 1000),
        },
    }


class TestRevenueCatPlanParsing:
    def test_plan_from_active_subscriber(self):
        expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        plan = plan_from_subscriber({"entitlements": {"pro": {"expires_date": expires}}}, "pro")
        assert plan == "pro"

    def test_plan_from_expired_subscriber(self):
        expires = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        plan = plan_from_subscriber({"entitlements": {"pro": {"expires_date": expires}}}, "pro")
        assert plan == "free"

    def test_lifetime_entitlement_is_pro(self):
        plan = plan_from_subscriber({"entitlements": {"pro": {"expires_date": None}}}, "pro")
        assert plan == "pro"

    def test_webhook_expiration_fallback_is_free(self):
        plan = plan_from_webhook_event(
            {"type": "EXPIRATION", "entitlement_ids": ["pro"], "expiration_at_ms": None},
            "pro",
        )
        assert plan == "free"

    def test_webhook_cancellation_fallback_does_not_grant_pro(self):
        plan = plan_from_webhook_event(
            {"type": "CANCELLATION", "entitlement_ids": ["pro"], "expiration_at_ms": None},
            "pro",
        )
        assert plan == "free"


class TestRevenueCatWebhook:
    def test_rejects_bad_authorization(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.webhooks.get_settings", lambda: _settings())
        r = client.post(
            "/webhooks/revenuecat",
            headers={"Authorization": "Bearer wrong"},
            json=_event("11111111-1111-1111-1111-111111111111"),
        )
        assert r.status_code == 401

    def test_updates_profile_to_pro_from_revenuecat_rest(
        self, client, fake_supabase, test_user_id, monkeypatch
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="free")
        monkeypatch.setattr("app.routers.webhooks.get_settings", lambda: _settings())

        async def fake_fetch_revenuecat_plan(app_user_id: str):
            assert app_user_id == str(test_user_id)
            return "pro"

        monkeypatch.setattr("app.routers.webhooks.fetch_revenuecat_plan", fake_fetch_revenuecat_plan)

        r = client.post(
            "/webhooks/revenuecat",
            headers={"Authorization": "Bearer webhook-secret"},
            json=_event(str(test_user_id)),
        )

        assert r.status_code == 200, r.json()
        assert r.json() == {"status": "ok", "plan": "pro"}
        assert fake_supabase.table("profiles").rows[0]["plan"] == "pro"

    def test_falls_back_to_event_when_rest_key_missing(
        self, client, fake_supabase, test_user_id, monkeypatch
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="free")
        monkeypatch.setattr(
            "app.routers.webhooks.get_settings",
            lambda: _settings(revenuecat_api_key=None),
        )

        async def fake_fetch_revenuecat_plan(app_user_id: str):
            raise RuntimeError("REVENUECAT_API_KEY is not configured")

        monkeypatch.setattr("app.routers.webhooks.fetch_revenuecat_plan", fake_fetch_revenuecat_plan)

        r = client.post(
            "/webhooks/revenuecat",
            headers={"Authorization": "Bearer webhook-secret"},
            json=_event(str(test_user_id)),
        )

        assert r.status_code == 200, r.json()
        assert r.json() == {"status": "ok", "plan": "pro"}
        assert fake_supabase.table("profiles").rows[0]["plan"] == "pro"

    def test_ignores_non_uuid_app_user_id(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.webhooks.get_settings", lambda: _settings())
        payload = _event("anonymous-revenuecat-id")

        r = client.post(
            "/webhooks/revenuecat",
            headers={"Authorization": "Bearer webhook-secret"},
            json=payload,
        )

        assert r.status_code == 200
        assert r.json()["status"] == "ignored"

    def test_revenuecat_http_error_returns_502(self, client, fake_supabase, test_user_id, monkeypatch):
        import httpx

        _seed_profile(fake_supabase, str(test_user_id), plan="free")
        monkeypatch.setattr("app.routers.webhooks.get_settings", lambda: _settings())

        async def fake_fetch_revenuecat_plan(app_user_id: str):
            raise httpx.ConnectError("boom")

        monkeypatch.setattr("app.routers.webhooks.fetch_revenuecat_plan", fake_fetch_revenuecat_plan)

        r = client.post(
            "/webhooks/revenuecat",
            headers={"Authorization": "Bearer webhook-secret"},
            json=_event(str(test_user_id)),
        )

        assert r.status_code == 502


def test_revenuecat_settings_defaults_are_safe():
    settings = _settings(revenuecat_webhook_auth=None, revenuecat_api_key=None)
    assert settings.revenuecat_webhook_auth is None
    assert settings.revenuecat_api_key is None
    assert settings.revenuecat_entitlement_id == "pro"
