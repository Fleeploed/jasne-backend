from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.services.trackers.base import CheckResult
from app.services.trackers.registry import TRACKER_REGISTRY
from app.workers.tracking_cron import mask_identifier, run_tracking_cron


def _seed_profile(fake_supabase, user_id: str, **overrides):
    row = {
        "id": user_id,
        "language": "ru",
        "plan": "pro",
        "has_datova_schranka": False,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    row.update(overrides)
    fake_supabase.table("profiles").rows.append(row)
    return row


def _seed_tracking(fake_supabase, user_id: str, **overrides):
    now = datetime.now(UTC)
    row = {
        "id": str(uuid4()),
        "user_id": user_id,
        "tracking_type": "oamp_status",
        "identifier": "OAM-19391/ZM-2026",
        "current_status": "probiha",
        "metadata": {},
        "last_checked_at": (now - timedelta(days=1)).isoformat(),
        "next_check_at": (now - timedelta(minutes=1)).isoformat(),
        "is_active": True,
        "notification_count": 0,
        "created_at": (now - timedelta(days=10)).isoformat(),
        "updated_at": now.isoformat(),
    }
    row.update(overrides)
    fake_supabase.table("trackings").rows.append(row)
    return row


def _patch_supabase(monkeypatch, fake_supabase):
    async def fake_get_supabase():
        return fake_supabase

    monkeypatch.setattr("app.workers.tracking_cron.get_supabase", fake_get_supabase)
    monkeypatch.setattr("app.services.push.get_supabase", fake_get_supabase)


@pytest.mark.asyncio
async def test_cron_processes_due_rows_and_final_status(fake_supabase, monkeypatch, test_user_id):
    _seed_profile(fake_supabase, str(test_user_id), language="ru")
    row = _seed_tracking(fake_supabase, str(test_user_id))
    _seed_tracking(
        fake_supabase,
        str(test_user_id),
        identifier="OAM-222/ZM-2026",
        next_check_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )
    _patch_supabase(monkeypatch, fake_supabase)
    tracker = TRACKER_REGISTRY["oamp_status"]

    async def fake_check(_identifier, _metadata):
        return CheckResult(status="schvaleno", raw_data={})

    pushes = []

    async def fake_push(user_id, tracking_type, identifier, status, title, body):
        pushes.append((user_id, tracking_type, identifier, status, title, body))
        return 1

    monkeypatch.setattr(tracker, "check", fake_check)
    monkeypatch.setattr("app.workers.tracking_cron.send_tracking_push", fake_push)

    stats = await run_tracking_cron(delay_seconds=0)

    assert stats["due"] == 1
    assert stats["checked"] == 1
    assert stats["changed"] == 1
    assert stats["deactivated"] == 1
    assert pushes[0][:4] == (str(test_user_id), "oamp_status", "OAM-19391/ZM-2026", "schvaleno")
    assert row["current_status"] == "schvaleno"
    assert row["is_active"] is False
    assert row["notification_count"] == 1


@pytest.mark.asyncio
async def test_cron_stops_after_three_not_found_checks(fake_supabase, monkeypatch, test_user_id):
    _seed_profile(fake_supabase, str(test_user_id), language="cs")
    row = _seed_tracking(
        fake_supabase,
        str(test_user_id),
        current_status="not_found",
        metadata={"not_found_count": 2, "error_count": 0},
    )
    _patch_supabase(monkeypatch, fake_supabase)
    tracker = TRACKER_REGISTRY["oamp_status"]

    async def fake_check(_identifier, metadata):
        return CheckResult(
            status="not_found_stopped",
            raw_data={"not_found_count": int(metadata["not_found_count"]) + 1, "error_count": 0},
        )

    pushes = []

    async def fake_push(_user_id, _tracking_type, _identifier, status, title, _body):
        pushes.append((status, title))
        return 1

    monkeypatch.setattr(tracker, "check", fake_check)
    monkeypatch.setattr("app.workers.tracking_cron.send_tracking_push", fake_push)

    stats = await run_tracking_cron(delay_seconds=0)

    assert stats["deactivated"] == 1
    assert pushes[0][0] == "not_found_stopped"
    assert row["is_active"] is False
    assert row["metadata"]["not_found_count"] == 3
    assert row["notification_count"] == 1


@pytest.mark.asyncio
async def test_cron_error_retry_keeps_tracking_active_under_threshold(
    fake_supabase, monkeypatch, test_user_id
):
    _seed_profile(fake_supabase, str(test_user_id), language="en")
    row = _seed_tracking(
        fake_supabase,
        str(test_user_id),
        current_status="error",
        metadata={"error_count": 3, "not_found_count": 0},
    )
    _patch_supabase(monkeypatch, fake_supabase)
    tracker = TRACKER_REGISTRY["oamp_status"]

    async def fake_check(_identifier, metadata):
        return CheckResult(
            status="error",
            raw_data={"error_count": int(metadata["error_count"]) + 1, "not_found_count": 0},
        )

    async def fake_push(*_args):
        raise AssertionError("Push should only be sent on status transitions")

    monkeypatch.setattr(tracker, "check", fake_check)
    monkeypatch.setattr("app.workers.tracking_cron.send_tracking_push", fake_push)

    stats = await run_tracking_cron(delay_seconds=0)

    assert stats["deactivated"] == 0
    assert row["is_active"] is True
    assert row["current_status"] == "error"
    assert row["metadata"]["error_count"] == 4
    assert datetime.fromisoformat(row["next_check_at"]) > datetime.now(UTC)


@pytest.mark.asyncio
async def test_cron_smart_frequency_for_unchanged_probiha(fake_supabase, monkeypatch, test_user_id):
    _seed_profile(fake_supabase, str(test_user_id))
    row = _seed_tracking(
        fake_supabase,
        str(test_user_id),
        current_status="probiha",
        created_at=(datetime.now(UTC) - timedelta(days=45)).isoformat(),
    )
    _patch_supabase(monkeypatch, fake_supabase)
    tracker = TRACKER_REGISTRY["oamp_status"]

    async def fake_check(_identifier, _metadata):
        return CheckResult(status="probiha", raw_data={"not_found_count": 0, "error_count": 0})

    monkeypatch.setattr(tracker, "check", fake_check)

    stats = await run_tracking_cron(delay_seconds=0)

    assert stats["checked"] == 1
    next_check = datetime.fromisoformat(row["next_check_at"])
    assert next_check > datetime.now(UTC) + timedelta(days=2)
    assert next_check < datetime.now(UTC) + timedelta(days=4)


def test_masks_identifier():
    assert mask_identifier("OAM-19391-4/ZM-2026") == "OAM-***/ZM-2026"
    assert mask_identifier("1234567890") == "123***7890"
