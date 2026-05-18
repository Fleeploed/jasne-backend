"""RevenueCat webhook and subscription sync tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config import Settings
from app.services.revenuecat import (
    SubscriptionState,
    plan_from_subscriber,
    plan_from_webhook_event,
    state_from_subscriber,
    state_from_webhook_event,
)


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
            "subscription_product_id": None,
            "subscription_expires_at": None,
            "subscription_will_renew": False,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )


def _event(
    user_id: str,
    event_type: str = "INITIAL_PURCHASE",
    event_id: str = "event-1",
    expires_at_ms: int | None = None,
    extra: dict | None = None,
) -> dict:
    event_body: dict = {
        "id": event_id,
        "type": event_type,
        "app_user_id": user_id,
        "entitlement_ids": ["pro"],
        "expiration_at_ms": (
            expires_at_ms
            if expires_at_ms is not None
            else int((datetime.now(UTC) + timedelta(days=30)).timestamp() * 1000)
        ),
        "product_id": "jasne_pro_monthly",
    }
    if extra:
        event_body.update(extra)
    return {"api_version": "1.0", "event": event_body}


# ---------------------------------------------------------------------------
# Pure state extraction
# ---------------------------------------------------------------------------


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

    def test_webhook_cancellation_fallback_does_not_grant_pro_when_unknown_expires(self):
        plan = plan_from_webhook_event(
            {"type": "CANCELLATION", "entitlement_ids": ["pro"], "expiration_at_ms": None},
            "pro",
        )
        assert plan == "free"

    def test_webhook_cancellation_with_future_expires_stays_pro(self):
        future_ms = int((datetime.now(UTC) + timedelta(days=10)).timestamp() * 1000)
        plan = plan_from_webhook_event(
            {"type": "CANCELLATION", "entitlement_ids": ["pro"], "expiration_at_ms": future_ms},
            "pro",
        )
        assert plan == "pro"

    def test_webhook_refund_is_immediate_free(self):
        future_ms = int((datetime.now(UTC) + timedelta(days=10)).timestamp() * 1000)
        plan = plan_from_webhook_event(
            {"type": "REFUND", "entitlement_ids": ["pro"], "expiration_at_ms": future_ms},
            "pro",
        )
        assert plan == "free"

    def test_webhook_subscription_paused_is_free(self):
        plan = plan_from_webhook_event(
            {"type": "SUBSCRIPTION_PAUSED", "entitlement_ids": ["pro"]},
            "pro",
        )
        assert plan == "free"


class TestSubscriptionStateExtraction:
    def test_subscriber_state_carries_product_and_will_renew(self):
        expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        state = state_from_subscriber(
            {
                "entitlements": {
                    "pro": {
                        "expires_date": expires,
                        "product_identifier": "jasne_pro_yearly",
                    }
                }
            },
            "pro",
        )
        assert state.plan == "pro"
        assert state.product_id == "jasne_pro_yearly"
        assert state.will_renew is True
        assert state.expires_at is not None

    def test_subscriber_state_cancelled_will_not_renew(self):
        expires = (datetime.now(UTC) + timedelta(days=10)).isoformat()
        state = state_from_subscriber(
            {
                "entitlements": {
                    "pro": {
                        "expires_date": expires,
                        "unsubscribe_detected_at": "2026-05-10T12:00:00Z",
                        "product_identifier": "jasne_pro_monthly",
                    }
                }
            },
            "pro",
        )
        assert state.plan == "pro"  # still active until expires
        assert state.will_renew is False

    def test_webhook_initial_purchase_state_has_will_renew_true(self):
        state = state_from_webhook_event(
            {
                "type": "INITIAL_PURCHASE",
                "entitlement_ids": ["pro"],
                "expiration_at_ms": int((datetime.now(UTC) + timedelta(days=30)).timestamp() * 1000),
                "product_id": "jasne_pro_monthly",
            },
            "pro",
        )
        assert state.plan == "pro"
        assert state.product_id == "jasne_pro_monthly"
        assert state.will_renew is True


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------


def _patch_dedup(monkeypatch, seen: set[str] | None = None):
    """Patch dedup helpers so tests don't depend on revenuecat_events table state."""
    seen = seen if seen is not None else set()

    async def fake_already(event_id: str) -> bool:
        return event_id in seen

    async def fake_record(**kwargs) -> None:
        eid = kwargs.get("event_id")
        if eid:
            seen.add(eid)

    monkeypatch.setattr("app.routers.webhooks.event_already_processed", fake_already)
    monkeypatch.setattr("app.routers.webhooks.record_event", fake_record)
    return seen


class TestRevenueCatWebhook:
    def test_rejects_bad_authorization(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.webhooks.get_settings", lambda: _settings())
        _patch_dedup(monkeypatch)
        r = client.post(
            "/webhooks/revenuecat",
            headers={"Authorization": "Bearer wrong"},
            json=_event("11111111-1111-1111-1111-111111111111"),
        )
        assert r.status_code == 401

    def test_test_event_returns_200_without_processing(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.webhooks.get_settings", lambda: _settings())
        _patch_dedup(monkeypatch)
        r = client.post(
            "/webhooks/revenuecat",
            headers={"Authorization": "Bearer webhook-secret"},
            json={"event": {"id": "test-1", "type": "TEST"}},
        )
        assert r.status_code == 200
        assert r.json()["reason"] == "test_event"

    def test_duplicate_event_is_ignored(self, client, fake_supabase, test_user_id, monkeypatch):
        _seed_profile(fake_supabase, str(test_user_id), plan="free")
        monkeypatch.setattr("app.routers.webhooks.get_settings", lambda: _settings())
        _patch_dedup(monkeypatch, seen={"dup-1"})

        r = client.post(
            "/webhooks/revenuecat",
            headers={"Authorization": "Bearer webhook-secret"},
            json=_event(str(test_user_id), event_id="dup-1"),
        )
        assert r.status_code == 200
        assert r.json()["reason"] == "duplicate"
        # Profile not touched
        assert fake_supabase.table("profiles").rows[0]["plan"] == "free"

    def test_updates_profile_to_pro_from_revenuecat_rest(
        self, client, fake_supabase, test_user_id, monkeypatch
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="free")
        monkeypatch.setattr("app.routers.webhooks.get_settings", lambda: _settings())
        _patch_dedup(monkeypatch)

        expires = datetime.now(UTC) + timedelta(days=30)

        async def fake_fetch(app_user_id: str):
            assert app_user_id == str(test_user_id)
            return SubscriptionState(
                plan="pro",
                product_id="jasne_pro_yearly",
                expires_at=expires,
                will_renew=True,
            )

        monkeypatch.setattr("app.routers.webhooks.fetch_subscription_state", fake_fetch)

        r = client.post(
            "/webhooks/revenuecat",
            headers={"Authorization": "Bearer webhook-secret"},
            json=_event(str(test_user_id)),
        )

        assert r.status_code == 200, r.json()
        assert r.json() == {"status": "ok", "plan": "pro"}
        row = fake_supabase.table("profiles").rows[0]
        assert row["plan"] == "pro"
        assert row["subscription_product_id"] == "jasne_pro_yearly"
        assert row["subscription_will_renew"] is True
        assert row["subscription_expires_at"] is not None

    def test_falls_back_to_event_when_rest_key_missing(
        self, client, fake_supabase, test_user_id, monkeypatch
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="free")
        monkeypatch.setattr(
            "app.routers.webhooks.get_settings",
            lambda: _settings(revenuecat_api_key=None),
        )
        _patch_dedup(monkeypatch)

        async def fake_fetch(app_user_id: str):
            raise RuntimeError("REVENUECAT_API_KEY is not configured")

        monkeypatch.setattr("app.routers.webhooks.fetch_subscription_state", fake_fetch)

        r = client.post(
            "/webhooks/revenuecat",
            headers={"Authorization": "Bearer webhook-secret"},
            json=_event(str(test_user_id)),
        )

        assert r.status_code == 200, r.json()
        assert r.json() == {"status": "ok", "plan": "pro"}
        row = fake_supabase.table("profiles").rows[0]
        assert row["plan"] == "pro"
        assert row["subscription_product_id"] == "jasne_pro_monthly"

    def test_ignores_non_uuid_app_user_id(self, client, monkeypatch):
        monkeypatch.setattr("app.routers.webhooks.get_settings", lambda: _settings())
        _patch_dedup(monkeypatch)
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
        _patch_dedup(monkeypatch)

        async def fake_fetch(app_user_id: str):
            raise httpx.ConnectError("boom")

        monkeypatch.setattr("app.routers.webhooks.fetch_subscription_state", fake_fetch)

        r = client.post(
            "/webhooks/revenuecat",
            headers={"Authorization": "Bearer webhook-secret"},
            json=_event(str(test_user_id)),
        )

        assert r.status_code == 502

    def test_refund_event_drops_plan_to_free(self, client, fake_supabase, test_user_id, monkeypatch):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        monkeypatch.setattr("app.routers.webhooks.get_settings", lambda: _settings())
        _patch_dedup(monkeypatch)

        async def fake_fetch(app_user_id: str):
            return SubscriptionState(plan="free")

        monkeypatch.setattr("app.routers.webhooks.fetch_subscription_state", fake_fetch)

        r = client.post(
            "/webhooks/revenuecat",
            headers={"Authorization": "Bearer webhook-secret"},
            json=_event(str(test_user_id), event_type="REFUND"),
        )

        assert r.status_code == 200
        assert fake_supabase.table("profiles").rows[0]["plan"] == "free"


def test_revenuecat_settings_defaults_are_safe():
    settings = _settings(revenuecat_webhook_auth=None, revenuecat_api_key=None)
    assert settings.revenuecat_webhook_auth is None
    assert settings.revenuecat_api_key is None
    assert settings.revenuecat_entitlement_id == "pro"
