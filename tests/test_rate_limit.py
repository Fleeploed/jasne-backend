"""Tests for the in-memory scan attempt rate limiter."""

from app.services.rate_limit import check_rate_limit, reset


class TestCheckRateLimit:
    def setup_method(self):
        reset()

    def test_allows_up_to_limit(self):
        for _ in range(3):
            assert check_rate_limit("u1", limit=3, window_seconds=3600) is True
        assert check_rate_limit("u1", limit=3, window_seconds=3600) is False

    def test_keys_are_isolated(self):
        for _ in range(3):
            check_rate_limit("u1", limit=3, window_seconds=3600)
        assert check_rate_limit("u2", limit=3, window_seconds=3600) is True

    def test_window_expiry_frees_slots(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr("app.services.rate_limit.time.monotonic", lambda: now[0])

        for _ in range(2):
            assert check_rate_limit("u1", limit=2, window_seconds=60) is True
        assert check_rate_limit("u1", limit=2, window_seconds=60) is False

        now[0] += 61
        assert check_rate_limit("u1", limit=2, window_seconds=60) is True

    def test_rejected_attempts_do_not_extend_lockout(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr("app.services.rate_limit.time.monotonic", lambda: now[0])

        check_rate_limit("u1", limit=1, window_seconds=60)
        # Hammering while limited must not push the recovery point forward.
        for _ in range(5):
            now[0] += 10
            check_rate_limit("u1", limit=1, window_seconds=60)
        now[0] = 1061.0
        assert check_rate_limit("u1", limit=1, window_seconds=60) is True


class TestScanRateLimitEndpoint:
    def test_429_when_over_limit(self, client, fake_supabase, auth_headers, monkeypatch):
        from app.config import get_settings

        monkeypatch.setattr(get_settings(), "scan_attempts_per_hour", 2)

        # Invalid body is fine — the rate limit check runs before everything
        # else that could fail, and attempts count regardless of outcome.
        for _ in range(2):
            r = client.post("/scans", headers=auth_headers, json={"image_paths": ["x/p.jpg"]})
            assert r.status_code != 429

        r = client.post("/scans", headers=auth_headers, json={"image_paths": ["x/p.jpg"]})
        assert r.status_code == 429
        assert "Too many scan attempts" in r.json()["detail"]
