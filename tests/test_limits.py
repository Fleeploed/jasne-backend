"""Tests for usage limits service (Free vs Pro caps)."""

from uuid import UUID

import pytest

from app.services.limits import (
    PLAN_LIMITS,
    caps_for_plan,
    check_chat_quota,
    check_scan_quota,
    current_month,
    get_or_init_usage,
    increment_chat_count,
    increment_scan_count,
)

USER_ID = UUID("11111111-1111-1111-1111-111111111111")
SCAN_ID = UUID("22222222-2222-2222-2222-222222222222")


class TestPlanLimits:
    def test_skill_caps_match(self):
        assert PLAN_LIMITS["free"]["monthly_scans"] == 5
        assert PLAN_LIMITS["free"]["max_pages_per_scan"] == 3
        assert PLAN_LIMITS["free"]["max_questions_per_scan"] == 0
        assert PLAN_LIMITS["pro"]["monthly_scans"] == 100
        assert PLAN_LIMITS["pro"]["max_pages_per_scan"] == 10
        assert PLAN_LIMITS["pro"]["max_questions_per_scan"] == 10

    def test_caps_for_plan_unknown_falls_back_to_free(self):
        caps = caps_for_plan("enterprise")  # type: ignore[arg-type]
        assert caps == PLAN_LIMITS["free"]


@pytest.fixture
def patched_supabase(fake_supabase, monkeypatch):
    async def fake_get_supabase():
        return fake_supabase

    monkeypatch.setattr("app.services.limits.get_supabase", fake_get_supabase)
    monkeypatch.setattr("app.services.rag.get_supabase", fake_get_supabase)
    return fake_supabase


class TestGetOrInitUsage:
    @pytest.mark.asyncio
    async def test_initializes_on_first_call(self, patched_supabase):
        usage = await get_or_init_usage(USER_ID)
        assert usage["scans_count"] == 0
        assert usage["questions_per_scan"] == {}
        assert usage["month"] == current_month()

    @pytest.mark.asyncio
    async def test_returns_existing_row(self, patched_supabase):
        # First call creates, second call should return same row
        u1 = await get_or_init_usage(USER_ID)
        u2 = await get_or_init_usage(USER_ID)
        assert u1["month"] == u2["month"]


class TestCheckScanQuota:
    @pytest.mark.asyncio
    async def test_free_user_within_limits(self, patched_supabase):
        await check_scan_quota(USER_ID, "free", page_count=2)  # no raise

    @pytest.mark.asyncio
    async def test_free_user_too_many_pages(self, patched_supabase):
        with pytest.raises(ValueError, match="max 3 pages"):
            await check_scan_quota(USER_ID, "free", page_count=5)

    @pytest.mark.asyncio
    async def test_pro_user_can_do_10_pages(self, patched_supabase):
        await check_scan_quota(USER_ID, "pro", page_count=10)

    @pytest.mark.asyncio
    async def test_blocks_after_monthly_limit(self, patched_supabase):
        # Exhaust free tier (5 scans/month)
        for _ in range(5):
            await increment_scan_count(USER_ID)
        with pytest.raises(ValueError, match="5 scans/month"):
            await check_scan_quota(USER_ID, "free", page_count=1)


class TestIncrementScanCount:
    @pytest.mark.asyncio
    async def test_increments_from_zero(self, patched_supabase):
        new = await increment_scan_count(USER_ID)
        assert new == 1
        new = await increment_scan_count(USER_ID)
        assert new == 2

    @pytest.mark.asyncio
    async def test_uses_atomic_rpc(self, patched_supabase, monkeypatch):
        calls = []
        original_rpc = patched_supabase.rpc

        def spy_rpc(name, params):
            calls.append((name, params))
            return original_rpc(name, params)

        monkeypatch.setattr(patched_supabase, "rpc", spy_rpc)
        new = await increment_scan_count(USER_ID)
        assert new == 1
        assert calls[0][0] == "increment_scan_count"


class TestCheckChatQuota:
    @pytest.mark.asyncio
    async def test_free_user_blocked(self, patched_supabase):
        with pytest.raises(ValueError, match="does not include chat"):
            await check_chat_quota(USER_ID, "free", SCAN_ID)

    @pytest.mark.asyncio
    async def test_pro_user_within_limits(self, patched_supabase):
        used = await check_chat_quota(USER_ID, "pro", SCAN_ID)
        assert used == 0

    @pytest.mark.asyncio
    async def test_pro_user_blocked_at_10(self, patched_supabase):
        for _ in range(10):
            await increment_chat_count(USER_ID, SCAN_ID)
        with pytest.raises(ValueError, match="10 questions per"):
            await check_chat_quota(USER_ID, "pro", SCAN_ID)

    @pytest.mark.asyncio
    async def test_chat_count_isolated_per_scan(self, patched_supabase):
        from uuid import uuid4

        other_scan = uuid4()
        for _ in range(10):
            await increment_chat_count(USER_ID, SCAN_ID)
        # Other scan still has 0
        used = await check_chat_quota(USER_ID, "pro", other_scan)
        assert used == 0

    @pytest.mark.asyncio
    async def test_chat_increment_uses_atomic_rpc(self, patched_supabase, monkeypatch):
        calls = []
        original_rpc = patched_supabase.rpc

        def spy_rpc(name, params):
            calls.append((name, params))
            return original_rpc(name, params)

        monkeypatch.setattr(patched_supabase, "rpc", spy_rpc)
        new = await increment_chat_count(USER_ID, SCAN_ID)
        assert new == 1
        assert calls[0][0] == "increment_chat_count"
