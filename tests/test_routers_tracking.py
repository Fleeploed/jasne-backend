from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.services.trackers.base import CheckResult
from app.services.trackers.registry import TRACKER_REGISTRY


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


def _seed_tracking(fake_supabase, user_id: str, **overrides):
    row = {
        "id": str(uuid4()),
        "user_id": user_id,
        "tracking_type": "oamp_status",
        "identifier": "OAM-19391/ZM-2026",
        "current_status": "probiha",
        "metadata": {},
        "last_checked_at": datetime.now(UTC).isoformat(),
        "next_check_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "is_active": True,
        "notification_count": 0,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    row.update(overrides)
    fake_supabase.table("trackings").rows.append(row)
    return row


class TestCreateTracking:
    def test_requires_pro_plan(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), plan="free")

        r = client.post(
            "/trackings",
            headers=auth_headers,
            json={"tracking_type": "oamp_status", "identifier": "OAM-19391/ZM-2026"},
        )

        assert r.status_code == 402
        assert fake_supabase.table("trackings").rows == []

    def test_creates_tracking_and_checks_status(
        self, client, fake_supabase, auth_headers, test_user_id, monkeypatch
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        calls = []
        tracker = TRACKER_REGISTRY["oamp_status"]

        async def fake_check(identifier: str, metadata: dict):
            calls.append((identifier, metadata))
            return CheckResult(status="probiha", raw_data={"not_found_count": 0, "error_count": 0})

        monkeypatch.setattr(tracker, "check", fake_check)

        r = client.post(
            "/trackings",
            headers=auth_headers,
            json={"tracking_type": "oamp_status", "identifier": " oam-19391/zm-2026 "},
        )

        assert r.status_code == 201, r.text
        body = r.json()
        assert body["tracking_type"] == "oamp_status"
        assert body["identifier"] == "OAM-19391/ZM-2026"
        assert body["current_status"] == "probiha"
        assert body["next_check_at"]
        assert calls == [("OAM-19391/ZM-2026", {})]

        rows = fake_supabase.table("trackings").rows
        assert len(rows) == 1
        assert rows[0]["is_active"] is True
        assert rows[0]["last_checked_at"] is not None

    def test_rejects_invalid_identifier(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")

        r = client.post(
            "/trackings",
            headers=auth_headers,
            json={"tracking_type": "oamp_status", "identifier": "19391"},
        )

        assert r.status_code == 422

    def test_rejects_more_than_five_active_of_same_type(
        self, client, fake_supabase, auth_headers, test_user_id
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        for index in range(5):
            _seed_tracking(fake_supabase, str(test_user_id), identifier=f"OAM-{index + 1}/ZM-2026")

        r = client.post(
            "/trackings",
            headers=auth_headers,
            json={"tracking_type": "oamp_status", "identifier": "OAM-999/ZM-2026"},
        )

        assert r.status_code == 429

    def test_duplicate_active_tracking_is_idempotent(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        existing = _seed_tracking(fake_supabase, str(test_user_id), identifier="OAM-19391/ZM-2026")

        r = client.post(
            "/trackings",
            headers=auth_headers,
            json={"tracking_type": "oamp_status", "identifier": "OAM-19391/ZM-2026"},
        )

        assert r.status_code == 200
        assert r.json()["id"] == existing["id"]
        assert len(fake_supabase.table("trackings").rows) == 1


class TestListTracking:
    def test_returns_only_active_user_trackings(self, client, fake_supabase, auth_headers, test_user_id):
        own = _seed_tracking(fake_supabase, str(test_user_id), identifier="OAM-1/ZM-2026")
        _seed_tracking(fake_supabase, str(test_user_id), identifier="OAM-2/ZM-2026", is_active=False)
        _seed_tracking(
            fake_supabase,
            "99999999-9999-9999-9999-999999999999",
            identifier="OAM-3/ZM-2026",
        )

        r = client.get("/trackings", headers=auth_headers)

        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["id"] == own["id"]

    def test_filters_by_type(self, client, fake_supabase, auth_headers, test_user_id):
        own = _seed_tracking(fake_supabase, str(test_user_id), tracking_type="oamp_status")
        _seed_tracking(fake_supabase, str(test_user_id), tracking_type="tax_status", identifier="TAX-1")

        r = client.get("/trackings?type=oamp_status", headers=auth_headers)

        assert r.status_code == 200
        assert [row["id"] for row in r.json()] == [own["id"]]


class TestGetTracking:
    def test_get_by_id(self, client, fake_supabase, auth_headers, test_user_id):
        row = _seed_tracking(fake_supabase, str(test_user_id))

        r = client.get(f"/trackings/{row['id']}", headers=auth_headers)

        assert r.status_code == 200
        assert r.json()["id"] == row["id"]


class TestDeleteTracking:
    def test_soft_deletes_own_tracking(self, client, fake_supabase, auth_headers, test_user_id):
        row = _seed_tracking(fake_supabase, str(test_user_id))

        r = client.delete(f"/trackings/{row['id']}", headers=auth_headers)

        assert r.status_code == 204
        assert fake_supabase.table("trackings").rows[0]["is_active"] is False

    def test_delete_unknown_returns_404(self, client, auth_headers):
        r = client.delete(f"/trackings/{uuid4()}", headers=auth_headers)

        assert r.status_code == 404
