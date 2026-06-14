"""Tests for the in-memory scan idempotency store."""

from app.services.idempotency import claim, complete, release, reset


class TestIdempotencyStore:
    def setup_method(self):
        reset()

    def test_first_claim_succeeds(self):
        assert claim("u1", "k1") is None  # claimed

    def test_second_claim_sees_pending(self):
        claim("u1", "k1")
        entry = claim("u1", "k1")
        assert entry is not None
        assert entry["state"] == "pending"
        assert entry["scan_id"] is None

    def test_completed_claim_returns_scan_id(self):
        claim("u1", "k1")
        complete("u1", "k1", "scan-abc")
        entry = claim("u1", "k1")
        assert entry is not None
        assert entry["state"] == "done"
        assert entry["scan_id"] == "scan-abc"

    def test_release_lets_retry_proceed(self):
        claim("u1", "k1")
        release("u1", "k1")
        assert claim("u1", "k1") is None  # fresh claim after release

    def test_keys_are_user_scoped(self):
        claim("u1", "k1")
        assert claim("u2", "k1") is None  # different user, independent
