from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from app.services.push_notifications import send_due_deadline_notifications


def _seed_profile(fake_supabase, user_id: str, **overrides):
    row = {
        "id": user_id,
        "display_name": None,
        "language": "ru",
        "has_datova_schranka": False,
        "plan": "pro",
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
        "expo_push_token": "ExponentPushToken[test-token]",
        "platform": "ios",
        "timezone": "Europe/Prague",
        "locale": "ru",
        "enabled": True,
        "created_at": datetime.now(UTC).isoformat(),
        "last_seen_at": datetime.now(UTC).isoformat(),
    }
    row.update(overrides)
    fake_supabase.table("push_tokens").rows.append(row)
    return row


def _seed_deadline(fake_supabase, user_id: str, due_date: date, **overrides):
    row = {
        "id": str(uuid4()),
        "user_id": user_id,
        "scan_id": str(uuid4()),
        "title": "Оплатить налог",
        "description": None,
        "due_date": due_date.isoformat(),
        "reminder_days": [30, 14, 7, 1],
        "deadline_type": "payment",
        "status": "active",
        "auto_created": True,
        "created_at": datetime.now(UTC).isoformat(),
    }
    row.update(overrides)
    fake_supabase.table("deadlines").rows.append(row)
    return row


def _patch_supabase(monkeypatch, fake_supabase):
    async def fake_get_supabase():
        return fake_supabase

    monkeypatch.setattr("app.services.push_notifications.get_supabase", fake_get_supabase)


@pytest.mark.asyncio
async def test_sends_due_pro_deadline_once(fake_supabase, monkeypatch, test_user_id):
    today = date(2026, 5, 30)
    _seed_profile(fake_supabase, str(test_user_id), plan="pro", language="ru")
    _seed_token(fake_supabase, str(test_user_id))
    _seed_deadline(fake_supabase, str(test_user_id), today + timedelta(days=14))
    _patch_supabase(monkeypatch, fake_supabase)

    sent_messages = []

    async def fake_post(_client, _url, messages):
        sent_messages.extend(messages)
        return [{"status": "ok", "id": "ticket-1"} for _ in messages]

    monkeypatch.setattr("app.services.push_notifications._post_expo_messages", fake_post)

    stats = await send_due_deadline_notifications(today)

    assert stats["scanned_deadlines"] == 1
    assert stats["candidate_notifications"] == 1
    assert stats["attempted"] == 1
    assert stats["sent"] == 1
    assert sent_messages[0]["title"] == "Напоминание: Оплатить налог"
    assert sent_messages[0]["data"]["type"] == "deadline_reminder"
    deliveries = fake_supabase.table("notification_deliveries").rows
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "sent"

    second_stats = await send_due_deadline_notifications(today)

    assert second_stats["attempted"] == 0
    assert len(fake_supabase.table("notification_deliveries").rows) == 1


@pytest.mark.asyncio
async def test_free_plan_only_sends_one_day_reminders(fake_supabase, monkeypatch, test_user_id):
    today = date(2026, 5, 30)
    _seed_profile(fake_supabase, str(test_user_id), plan="free", language="ru")
    _seed_token(fake_supabase, str(test_user_id))
    _seed_deadline(fake_supabase, str(test_user_id), today + timedelta(days=30))
    _patch_supabase(monkeypatch, fake_supabase)

    async def fake_post(_client, _url, messages):
        raise AssertionError("Free users should not receive 30-day push reminders")

    monkeypatch.setattr("app.services.push_notifications._post_expo_messages", fake_post)

    stats = await send_due_deadline_notifications(today)

    assert stats["candidate_notifications"] == 0
    assert stats["attempted"] == 0
    assert fake_supabase.table("notification_deliveries").rows == []
