"""Tests for /health (liveness) and /health/deep (readiness with dep checks)."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestLivenessHealth:
    def test_returns_200_with_status_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert "environment" in body
        assert "version" in body

    def test_does_not_touch_supabase(self, client, monkeypatch):
        """Liveness must NOT depend on Supabase — that's what /health/deep is for."""

        async def boom():
            raise RuntimeError("supabase should not be called from /health")

        # If liveness queries Supabase this will raise → 500.
        monkeypatch.setattr("app.main.get_supabase", boom)
        r = client.get("/health")
        assert r.status_code == 200


class TestDeepHealth:
    def test_all_deps_ok_returns_200(self, client):
        r = client.get("/health/deep")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert set(body["deps"].keys()) == {"supabase", "openai"}
        for name, dep in body["deps"].items():
            assert dep["status"] == "ok", f"{name} should be ok"
            assert dep["latency_ms"] is not None
            assert dep["error"] is None

    def test_supabase_down_returns_503(self, client, monkeypatch):
        async def broken_supabase():
            raise ConnectionError("connection refused")

        monkeypatch.setattr("app.main.get_supabase", broken_supabase)

        r = client.get("/health/deep")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert body["deps"]["supabase"]["status"] == "error"
        assert "ConnectionError" in body["deps"]["supabase"]["error"]
        # OpenAI is independent — should still be ok
        assert body["deps"]["openai"]["status"] == "ok"

    def test_openai_down_returns_503(self, client, monkeypatch):
        broken_client = MagicMock()
        broken_client.models.list = AsyncMock(side_effect=RuntimeError("OpenAI 503"))
        monkeypatch.setattr("app.main.get_openai_client", lambda: broken_client)

        r = client.get("/health/deep")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert body["deps"]["openai"]["status"] == "error"
        assert "RuntimeError" in body["deps"]["openai"]["error"]
        assert body["deps"]["supabase"]["status"] == "ok"

    def test_both_deps_down_returns_503(self, client, monkeypatch):
        async def broken_supabase():
            raise ConnectionError("nope")

        broken_openai = MagicMock()
        broken_openai.models.list = AsyncMock(side_effect=RuntimeError("nope"))

        monkeypatch.setattr("app.main.get_supabase", broken_supabase)
        monkeypatch.setattr("app.main.get_openai_client", lambda: broken_openai)

        r = client.get("/health/deep")
        assert r.status_code == 503
        body = r.json()
        assert body["status"] == "degraded"
        assert body["deps"]["supabase"]["status"] == "error"
        assert body["deps"]["openai"]["status"] == "error"

    def test_error_messages_are_truncated(self, client, monkeypatch):
        """Avoid leaking long stack traces / credentials in the response body."""
        long_msg = "x" * 5000

        async def broken_supabase():
            raise RuntimeError(long_msg)

        monkeypatch.setattr("app.main.get_supabase", broken_supabase)

        r = client.get("/health/deep")
        assert r.status_code == 503
        # Truncated to <=200 chars in the handler
        assert len(r.json()["deps"]["supabase"]["error"]) <= 200

    @pytest.mark.asyncio
    async def test_supabase_timeout_reported_as_error(self, monkeypatch):
        """Verify the asyncio.wait_for timeout path produces a clean error message."""
        import asyncio

        from app.main import _check_supabase

        class HangingSupabase:
            def table(self, name):
                return self

            def select(self, *a, **kw):
                return self

            def limit(self, n):
                return self

            async def execute(self):
                await asyncio.sleep(60)  # longer than the 5s deep-probe timeout

        async def fake_get():
            return HangingSupabase()

        monkeypatch.setattr("app.main.get_supabase", fake_get)
        # Shrink the timeout so the test runs fast
        monkeypatch.setattr("app.main._DEEP_DEP_TIMEOUT_SECONDS", 0.1)

        result = await _check_supabase()
        assert result.status == "error"
        assert "timeout" in result.error.lower()
