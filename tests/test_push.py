"""Tests for the tracking push service (app/services/push.py)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.services.push import _push_tokens_for_user, send_tracking_push


def _seed_profile(fake_supabase, user_id: str, **overrides):
    row = {
        "id": user_id,
        "display_name": None,
        "language": "ru",
        "has_datova_schranka": False,
        "plan": "pro",
        "expo_push_token": "ExponentPushToken[profile-token]",
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    row.update(overrides)
    fake_supabase.table("profiles").rows.append(row)
    return row


def _seed_token(fake_supabase, user_id: str, **overrides):
    row = {
        "id": str(uuid4()),
        "user_id": user_id,
        "expo_push_token": "ExponentPushToken[device-token]",
        "platform": "ios",
        "enabled": True,
        "created_at": datetime.now(UTC).isoformat(),
    }
    row.update(overrides)
    fake_supabase.table("push_tokens").rows.append(row)
    return row


def _patch_supabase(monkeypatch, fake_supabase):
    async def fake_get_supabase():
        return fake_supabase

    monkeypatch.setattr("app.services.push.get_supabase", fake_get_supabase)


@pytest.mark.asyncio
async def test_collects_profile_and_device_tokens(fake_supabase, monkeypatch, test_user_id):
    _seed_profile(fake_supabase, str(test_user_id))
    _seed_token(fake_supabase, str(test_user_id))
    _patch_supabase(monkeypatch, fake_supabase)

    tokens = await _push_tokens_for_user(str(test_user_id))

    assert tokens == [
        "ExponentPushToken[profile-token]",
        "ExponentPushToken[device-token]",
    ]


@pytest.mark.asyncio
async def test_pending_deletion_user_gets_no_tokens(fake_supabase, monkeypatch, test_user_id):
    """push_tokens rows stay enabled during the 7-day grace period (for a clean
    restore), so the token lookup must filter on profiles.deletion_status."""
    _seed_profile(fake_supabase, str(test_user_id), deletion_status="pending")
    _seed_token(fake_supabase, str(test_user_id))
    _patch_supabase(monkeypatch, fake_supabase)

    assert await _push_tokens_for_user(str(test_user_id)) == []


@pytest.mark.asyncio
async def test_send_tracking_push_skips_pending_deletion(fake_supabase, monkeypatch, test_user_id):
    _seed_profile(fake_supabase, str(test_user_id), deletion_status="pending")
    _seed_token(fake_supabase, str(test_user_id))
    _patch_supabase(monkeypatch, fake_supabase)

    class NoPostClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise AssertionError("No push should be sent to an account pending deletion")

    monkeypatch.setattr("app.services.push.httpx.AsyncClient", lambda **kwargs: NoPostClient())

    sent = await send_tracking_push(
        str(test_user_id),
        tracking_type="oamp",
        identifier="OAM-123",
        status="granted",
        title="Решение готово",
        body="Заявление одобрено.",
    )

    assert sent == 0
