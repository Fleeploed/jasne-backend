"""End-to-end router tests for /deadlines."""

from datetime import UTC, datetime
from uuid import uuid4


def _seed_deadline(fake_supabase, user_id: str, **overrides):
    row = {
        "id": str(uuid4()),
        "user_id": user_id,
        "scan_id": None,
        "title": "pay faktura",
        "description": None,
        "due_date": "2026-06-01",
        "reminder_days": [30, 14, 7, 1],
        "deadline_type": "payment",
        "status": "active",
        "created_at": datetime.now(UTC).isoformat(),
    }
    row.update(overrides)
    fake_supabase.table("deadlines").rows.append(row)
    return row


def _seed_scan(fake_supabase, user_id: str, **overrides):
    row = {
        "id": str(uuid4()),
        "user_id": user_id,
        "image_paths": [f"{user_id}/doc.jpg"],
        "page_count": 1,
        "ocr_text": "ocr",
        "category": "ucty",
        "doc_type": "faktura",
        "category_confidence": 0.9,
        "result": {},
        "language": "cs",
        "model_used": "gpt-4o-mini",
        "created_at": datetime.now(UTC).isoformat(),
    }
    row.update(overrides)
    fake_supabase.table("scans").rows.append(row)
    return row


class TestListDeadlines:
    def test_empty_list(self, client, auth_headers):
        r = client.get("/deadlines", headers=auth_headers)
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_own(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_deadline(fake_supabase, str(test_user_id), title="a")
        _seed_deadline(fake_supabase, str(test_user_id), title="b")
        r = client.get("/deadlines", headers=auth_headers)
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_filters_by_status_when_active(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_deadline(fake_supabase, str(test_user_id), title="active1")
        _seed_deadline(fake_supabase, str(test_user_id), title="done1", status="completed")
        r = client.get("/deadlines?only_active=true", headers=auth_headers)
        assert r.status_code == 200
        titles = [d["title"] for d in r.json()]
        assert "active1" in titles
        assert "done1" not in titles

    def test_all_when_inactive_filter_off(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_deadline(fake_supabase, str(test_user_id), title="active1")
        _seed_deadline(fake_supabase, str(test_user_id), title="done1", status="completed")
        r = client.get("/deadlines?only_active=false", headers=auth_headers)
        assert len(r.json()) == 2

    def test_does_not_return_others(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_deadline(fake_supabase, str(test_user_id), title="mine")
        _seed_deadline(fake_supabase, "99999999-9999-9999-9999-999999999999", title="alice")
        r = client.get("/deadlines", headers=auth_headers)
        assert len(r.json()) == 1
        assert r.json()[0]["title"] == "mine"

    def test_paginates(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_deadline(fake_supabase, str(test_user_id), title="first", due_date="2026-06-01")
        _seed_deadline(fake_supabase, str(test_user_id), title="second", due_date="2026-06-02")
        _seed_deadline(fake_supabase, str(test_user_id), title="third", due_date="2026-06-03")
        r = client.get("/deadlines?limit=1&offset=1", headers=auth_headers)
        assert r.status_code == 200
        assert [d["title"] for d in r.json()] == ["second"]

    def test_filters_by_status_filter(self, client, fake_supabase, auth_headers, test_user_id):
        _seed_deadline(fake_supabase, str(test_user_id), title="active", status="active")
        _seed_deadline(
            fake_supabase,
            str(test_user_id),
            title="done-newer",
            status="completed",
            due_date="2026-06-03",
        )
        _seed_deadline(
            fake_supabase,
            str(test_user_id),
            title="done-older",
            status="completed",
            due_date="2026-06-01",
        )
        r = client.get("/deadlines?status_filter=completed&limit=10&offset=0", headers=auth_headers)
        assert r.status_code == 200
        assert [d["title"] for d in r.json()] == ["done-newer", "done-older"]


class TestGetDeadline:
    def test_get_by_id(self, client, fake_supabase, auth_headers, test_user_id):
        row = _seed_deadline(fake_supabase, str(test_user_id))
        r = client.get(f"/deadlines/{row['id']}", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["id"] == row["id"]

    def test_not_found(self, client, auth_headers):
        r = client.get(f"/deadlines/{uuid4()}", headers=auth_headers)
        assert r.status_code == 404


class TestCreateDeadline:
    def test_create(self, client, auth_headers):
        r = client.post(
            "/deadlines",
            headers=auth_headers,
            json={"title": "pay tax", "due_date": "2026-05-15", "deadline_type": "payment"},
        )
        assert r.status_code == 201
        assert r.json()["title"] == "pay tax"
        assert r.json()["status"] == "active"

    def test_create_rejects_foreign_scan_id(self, client, fake_supabase, auth_headers):
        other_scan = _seed_scan(fake_supabase, "99999999-9999-9999-9999-999999999999")
        r = client.post(
            "/deadlines",
            headers=auth_headers,
            json={
                "scan_id": other_scan["id"],
                "title": "pay tax",
                "due_date": "2026-05-15",
                "deadline_type": "payment",
            },
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "Scan not found"

    def test_create_allows_own_scan_id(self, client, fake_supabase, auth_headers, test_user_id):
        own_scan = _seed_scan(fake_supabase, str(test_user_id))
        r = client.post(
            "/deadlines",
            headers=auth_headers,
            json={
                "scan_id": own_scan["id"],
                "title": "pay tax",
                "due_date": "2026-05-15",
                "deadline_type": "payment",
            },
        )
        assert r.status_code == 201
        assert r.json()["scan_id"] == own_scan["id"]

    def test_validation_missing_due_date(self, client, auth_headers):
        r = client.post("/deadlines", headers=auth_headers, json={"title": "x"})
        assert r.status_code == 422

    def test_validation_bad_type(self, client, auth_headers):
        r = client.post(
            "/deadlines",
            headers=auth_headers,
            json={"title": "x", "due_date": "2026-05-01", "deadline_type": "nonsense"},
        )
        assert r.status_code == 422


class TestUpdateDeadline:
    def test_patch_status(self, client, fake_supabase, auth_headers, test_user_id):
        row = _seed_deadline(fake_supabase, str(test_user_id))
        r = client.patch(
            f"/deadlines/{row['id']}",
            headers=auth_headers,
            json={"status": "completed"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

    def test_patch_empty_rejected(self, client, fake_supabase, auth_headers, test_user_id):
        row = _seed_deadline(fake_supabase, str(test_user_id))
        r = client.patch(f"/deadlines/{row['id']}", headers=auth_headers, json={})
        assert r.status_code == 422

    def test_patch_nonexistent(self, client, auth_headers):
        r = client.patch(f"/deadlines/{uuid4()}", headers=auth_headers, json={"status": "completed"})
        assert r.status_code == 404

    def test_patch_rejects_foreign_scan_id(self, client, fake_supabase, auth_headers, test_user_id):
        row = _seed_deadline(fake_supabase, str(test_user_id))
        other_scan = _seed_scan(fake_supabase, "99999999-9999-9999-9999-999999999999")
        r = client.patch(
            f"/deadlines/{row['id']}",
            headers=auth_headers,
            json={"scan_id": other_scan["id"]},
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "Scan not found"


class TestDeleteDeadline:
    def test_delete(self, client, fake_supabase, auth_headers, test_user_id):
        row = _seed_deadline(fake_supabase, str(test_user_id))
        r = client.delete(f"/deadlines/{row['id']}", headers=auth_headers)
        assert r.status_code == 204
        # Verify gone
        r2 = client.get(f"/deadlines/{row['id']}", headers=auth_headers)
        assert r2.status_code == 404
