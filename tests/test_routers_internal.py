"""Tests for /internal cron-only endpoints not covered elsewhere."""

from __future__ import annotations

import pytest


class TestTrackingCron:
    def test_runs_and_returns_stats(self, client, monkeypatch):
        from app.config import get_settings

        # Pin the secret so the test doesn't depend on the developer's local .env.
        monkeypatch.setattr(get_settings(), "internal_cron_secret", "expected-secret")

        async def fake_run_tracking_cron():
            return {
                "due": 3,
                "checked": 3,
                "changed": 1,
                "pushes_attempted": 1,
                "deactivated": 1,
                "errors": 0,
            }

        monkeypatch.setattr("app.routers.internal.run_tracking_cron", fake_run_tracking_cron)

        r = client.post(
            "/internal/trackings/run-cron",
            headers={"X-Cron-Secret": "expected-secret"},
        )

        assert r.status_code == 200, r.text
        assert r.json() == {
            "due": 3,
            "checked": 3,
            "changed": 1,
            "pushes_attempted": 1,
            "deactivated": 1,
            "errors": 0,
        }

    def test_rejects_wrong_secret(self, client, monkeypatch):
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "internal_cron_secret", "expected-secret")

        called = {"n": 0}

        async def fake_run_tracking_cron():
            called["n"] += 1
            return {
                "due": 0,
                "checked": 0,
                "changed": 0,
                "pushes_attempted": 0,
                "deactivated": 0,
                "errors": 0,
            }

        monkeypatch.setattr("app.routers.internal.run_tracking_cron", fake_run_tracking_cron)

        r = client.post(
            "/internal/trackings/run-cron",
            headers={"X-Cron-Secret": "wrong-secret"},
        )

        assert r.status_code == 403
        assert called["n"] == 0

    def test_accepts_correct_secret(self, client, monkeypatch):
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "internal_cron_secret", "expected-secret")

        async def fake_run_tracking_cron():
            return {
                "due": 1,
                "checked": 1,
                "changed": 0,
                "pushes_attempted": 0,
                "deactivated": 0,
                "errors": 0,
            }

        monkeypatch.setattr("app.routers.internal.run_tracking_cron", fake_run_tracking_cron)

        r = client.post(
            "/internal/trackings/run-cron",
            headers={"X-Cron-Secret": "expected-secret"},
        )

        assert r.status_code == 200, r.text
        assert r.json()["checked"] == 1


@pytest.mark.parametrize("path", ["/internal/notifications/due", "/internal/accounts/purge-deleted"])
def test_internal_endpoints_reject_wrong_secret(client, monkeypatch, path):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "internal_cron_secret", "expected-secret")

    r = client.post(path, headers={"X-Cron-Secret": "nope"})
    assert r.status_code == 403
