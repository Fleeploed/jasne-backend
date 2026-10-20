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
async def test_skips_deadlines_beyond_reminder_horizon(fake_supabase, monkeypatch, test_user_id):
    """A deadline further out than the longest reminder window (30 days) can't
    fire today, so the cron query must not even pull it."""
    today = date(2026, 5, 30)
    _seed_profile(fake_supabase, str(test_user_id), plan="pro", language="ru")
    _seed_token(fake_supabase, str(test_user_id))
    _seed_deadline(fake_supabase, str(test_user_id), today + timedelta(days=60))
    _patch_supabase(monkeypatch, fake_supabase)

    async def fake_post(_client, _url, messages):
        raise AssertionError("No push should be sent for a deadline 60 days out")

    monkeypatch.setattr("app.services.push_notifications._post_expo_messages", fake_post)

    stats = await send_due_deadline_notifications(today)

    assert stats["scanned_deadlines"] == 0
    assert stats["candidate_notifications"] == 0
    assert stats["attempted"] == 0


@pytest.mark.asyncio
async def test_skips_user_pending_deletion(fake_supabase, monkeypatch, test_user_id):
    """push_tokens rows stay enabled during the 7-day grace period (for a clean
    restore), so the cron must filter on profiles.deletion_status."""
    today = date(2026, 5, 30)
    _seed_profile(fake_supabase, str(test_user_id), plan="pro", language="ru", deletion_status="pending")
    _seed_token(fake_supabase, str(test_user_id))
    _seed_deadline(fake_supabase, str(test_user_id), today + timedelta(days=14))
    _patch_supabase(monkeypatch, fake_supabase)

    async def fake_post(_client, _url, messages):
        raise AssertionError("No push should be sent to an account pending deletion")

    monkeypatch.setattr("app.services.push_notifications._post_expo_messages", fake_post)

    stats = await send_due_deadline_notifications(today)

    assert stats["scanned_deadlines"] == 1
    assert stats["candidate_notifications"] == 0
    assert stats["attempted"] == 0
    assert stats["skipped"] == 1
    assert fake_supabase.table("notification_deliveries").rows == []


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


class TestCheckExpoPushReceipts:
    def _seed_delivery(self, fake_supabase, token_id, *, ticket="ticket-1", sent_minutes_ago=30):
        row = {
            "id": str(uuid4()),
            "user_id": str(uuid4()),
            "deadline_id": str(uuid4()),
            "push_token_id": token_id,
            "reminder_day": 1,
            "scheduled_for": "2026-06-12",
            "sent_at": (datetime.now(UTC) - timedelta(minutes=sent_minutes_ago)).isoformat(),
            "status": "sent",
            "expo_ticket_id": ticket,
            "expo_receipt_id": None,
            "error": None,
        }
        fake_supabase.table("notification_deliveries").rows.append(row)
        return row

    @pytest.mark.asyncio
    async def test_ok_receipt_marks_delivered(self, fake_supabase, monkeypatch, test_user_id):
        from app.services.push_notifications import check_expo_push_receipts

        token = _seed_token(fake_supabase, str(test_user_id))
        delivery = self._seed_delivery(fake_supabase, token["id"])
        _patch_supabase(monkeypatch, fake_supabase)

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": {"ticket-1": {"status": "ok"}}}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, **kwargs):
                assert kwargs["json"] == {"ids": ["ticket-1"]}
                return FakeResponse()

        monkeypatch.setattr("app.services.push_notifications.httpx.AsyncClient", lambda **kw: FakeClient())

        stats = await check_expo_push_receipts()

        assert stats == {"checked": 1, "delivered": 1, "failed": 0, "tokens_disabled": 0, "pending": 0}
        row = fake_supabase.table("notification_deliveries").rows[0]
        assert row["status"] == "receipt_ok"
        assert row["expo_receipt_id"] == "ticket-1"
        assert delivery["id"] == row["id"]

    @pytest.mark.asyncio
    async def test_device_not_registered_disables_token(self, fake_supabase, monkeypatch, test_user_id):
        from app.services.push_notifications import check_expo_push_receipts

        token = _seed_token(fake_supabase, str(test_user_id))
        self._seed_delivery(fake_supabase, token["id"])
        _patch_supabase(monkeypatch, fake_supabase)

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "data": {
                        "ticket-1": {
                            "status": "error",
                            "message": "device gone",
                            "details": {"error": "DeviceNotRegistered"},
                        }
                    }
                }

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, **kwargs):
                return FakeResponse()

        monkeypatch.setattr("app.services.push_notifications.httpx.AsyncClient", lambda **kw: FakeClient())

        stats = await check_expo_push_receipts()

        assert stats["failed"] == 1
        assert stats["tokens_disabled"] == 1
        assert fake_supabase.table("push_tokens").rows[0]["enabled"] is False
        assert fake_supabase.table("notification_deliveries").rows[0]["status"] == "error"

    @pytest.mark.asyncio
    async def test_missing_receipt_stays_sent_for_retry(self, fake_supabase, monkeypatch, test_user_id):
        from app.services.push_notifications import check_expo_push_receipts

        token = _seed_token(fake_supabase, str(test_user_id))
        self._seed_delivery(fake_supabase, token["id"])
        _patch_supabase(monkeypatch, fake_supabase)

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"data": {}}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, **kwargs):
                return FakeResponse()

        monkeypatch.setattr("app.services.push_notifications.httpx.AsyncClient", lambda **kw: FakeClient())

        stats = await check_expo_push_receipts()

        assert stats["pending"] == 1
        assert fake_supabase.table("notification_deliveries").rows[0]["status"] == "sent"

    @pytest.mark.asyncio
    async def test_fresh_deliveries_not_checked_yet(self, fake_supabase, monkeypatch, test_user_id):
        from app.services.push_notifications import check_expo_push_receipts

        token = _seed_token(fake_supabase, str(test_user_id))
        self._seed_delivery(fake_supabase, token["id"], sent_minutes_ago=5)
        _patch_supabase(monkeypatch, fake_supabase)

        def no_client(**kw):
            raise AssertionError("No receipts request expected for fresh deliveries")

        monkeypatch.setattr("app.services.push_notifications.httpx.AsyncClient", no_client)

        stats = await check_expo_push_receipts()

        assert stats == {"checked": 0, "delivered": 0, "failed": 0, "tokens_disabled": 0, "pending": 0}
