from datetime import UTC, datetime, timedelta
from uuid import uuid4


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
        "cislo_jednaci": "OAM-19391/ZM-2026",
        "current_status": "probiha",
        "last_checked_at": datetime.now(UTC).isoformat(),
        "next_check_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        "is_active": True,
        "notification_count": 0,
        "consecutive_not_found_count": 0,
        "consecutive_error_count": 0,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    row.update(overrides)
    fake_supabase.table("tracked_applications").rows.append(row)
    return row


class TestCreateTracking:
    def test_requires_pro_plan(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), plan="free")

        r = client.post(
            "/tracking/applications",
            headers=auth_headers,
            json={"cislo_jednaci": "OAM-19391/ZM-2026"},
        )

        assert r.status_code == 402
        assert fake_supabase.table("tracked_applications").rows == []

    def test_creates_tracking_and_checks_status(
        self, client, fake_supabase, auth_headers, test_user_id, monkeypatch
    ):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        calls = []

        async def fake_check_status(cislo_jednaci: str):
            calls.append(cislo_jednaci)
            return "probiha"

        monkeypatch.setattr("app.routers.tracking.check_status", fake_check_status)

        r = client.post(
            "/tracking/applications",
            headers=auth_headers,
            json={"cislo_jednaci": " oam-19391/zm-2026 "},
        )

        assert r.status_code == 201, r.text
        body = r.json()
        assert body["cislo_jednaci"] == "OAM-19391/ZM-2026"
        assert body["current_status"] == "probiha"
        assert body["next_check_at"]
        assert calls == ["OAM-19391/ZM-2026"]

        rows = fake_supabase.table("tracked_applications").rows
        assert len(rows) == 1
        assert rows[0]["is_active"] is True
        assert rows[0]["last_checked_at"] is not None

    def test_rejects_more_than_five_active(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        for index in range(5):
            _seed_tracking(fake_supabase, str(test_user_id), cislo_jednaci=f"OAM-{index + 1}/ZM-2026")

        r = client.post(
            "/tracking/applications",
            headers=auth_headers,
            json={"cislo_jednaci": "OAM-999/ZM-2026"},
        )

        assert r.status_code == 429

    def test_rejects_duplicate_active_tracking(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_profile(fake_supabase, str(test_user_id), plan="pro")
        _seed_tracking(fake_supabase, str(test_user_id), cislo_jednaci="OAM-19391/ZM-2026")

        r = client.post(
            "/tracking/applications",
            headers=auth_headers,
            json={"cislo_jednaci": "OAM-19391/ZM-2026"},
        )

        assert r.status_code == 409


class TestListTracking:
    def test_returns_only_active_user_trackings(self, client, fake_supabase, auth_headers, test_user_id):
        own = _seed_tracking(fake_supabase, str(test_user_id), cislo_jednaci="OAM-1/ZM-2026")
        _seed_tracking(fake_supabase, str(test_user_id), cislo_jednaci="OAM-2/ZM-2026", is_active=False)
        _seed_tracking(
            fake_supabase,
            "99999999-9999-9999-9999-999999999999",
            cislo_jednaci="OAM-3/ZM-2026",
        )

        r = client.get("/tracking/applications", headers=auth_headers)

        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["id"] == own["id"]


class TestDeleteTracking:
    def test_soft_deletes_own_tracking(self, client, fake_supabase, auth_headers, test_user_id):
        row = _seed_tracking(fake_supabase, str(test_user_id))

        r = client.delete(f"/tracking/applications/{row['id']}", headers=auth_headers)

        assert r.status_code == 204
        assert fake_supabase.table("tracked_applications").rows[0]["is_active"] is False

    def test_delete_unknown_returns_404(self, client, auth_headers):
        r = client.delete(f"/tracking/applications/{uuid4()}", headers=auth_headers)

        assert r.status_code == 404
