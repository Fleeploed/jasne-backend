from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.schemas import OampStatusCheckResponse
from app.services.oamp_status import (
    OAMP_STATUS_URL,
    OAMP_STATUS_URL_EN,
    OampStatusError,
    OampStatusTimeoutError,
    _classify_result,
    _status_url_for_language,
)


def test_oamp_status_requires_auth(client):
    r = client.post(
        "/status/oamp",
        json={"case_number": "OAM-19391-4/ZM-2026"},
    )

    assert r.status_code in (401, 403)


def test_oamp_status_accepts_full_case_number(client, auth_headers, monkeypatch):
    captured = {}

    def fake_check(payload):
        captured["payload"] = payload
        return OampStatusCheckResponse(
            kind="proceedings",
            query="OAM-19391-4/ZM-2026",
            status="found",
            message="Řízení či oznámení s číslem jednacím OAM-19391-4/ZM-2026 je v řízení.",
            source_url="https://ipc.gov.cz/informace-o-stavu-rizeni/",
            checked_at=datetime.now(UTC),
        )

    monkeypatch.setattr("app.routers.status.check_oamp_status", fake_check)

    r = client.post(
        "/status/oamp",
        headers=auth_headers,
        json={"case_number": "OAM-19391-4/ZM-2026", "language": "en"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "proceedings"
    assert body["query"] == "OAM-19391-4/ZM-2026"
    assert body["status"] == "found"

    payload = captured["payload"]
    assert payload.reference_number == "19391"
    assert payload.additional_suffix == "4"
    assert payload.category == "ZM"
    assert payload.year == "2026"
    assert payload.language == "en"


def test_oamp_status_accepts_segmented_case_fields(client, auth_headers, monkeypatch):
    captured = {}

    def fake_check(payload):
        captured["payload"] = payload
        return OampStatusCheckResponse(
            kind="proceedings",
            query="OAM-19391/ZM-2026",
            status="unknown",
            message="No parsed result",
            source_url="https://ipc.gov.cz/informace-o-stavu-rizeni/",
            checked_at=datetime.now(UTC),
        )

    monkeypatch.setattr("app.routers.status.check_oamp_status", fake_check)

    r = client.post(
        "/status/oamp",
        headers=auth_headers,
        json={"reference_number": "19391", "category": "ZM", "year": "2026"},
    )

    assert r.status_code == 200, r.text
    payload = captured["payload"]
    assert payload.reference_number == "19391"
    assert payload.additional_suffix is None
    assert payload.category == "ZM"
    assert payload.year == "2026"


def test_oamp_status_requires_query(client, auth_headers):
    r = client.post("/status/oamp", headers=auth_headers, json={})

    assert r.status_code == 422


@pytest.mark.parametrize(
    ("exc", "expected_status"),
    [
        (OampStatusTimeoutError("Timed out while checking OAMP status"), 504),
        (OampStatusError("Could not check OAMP status"), 502),
    ],
)
def test_oamp_status_maps_service_errors(client, auth_headers, monkeypatch, exc, expected_status):
    def fake_check(_payload):
        raise exc

    monkeypatch.setattr("app.routers.status.check_oamp_status", fake_check)

    r = client.post(
        "/status/oamp",
        headers=auth_headers,
        json={"case_number": "OAM-19391-4/ZM-2026"},
    )

    assert r.status_code == expected_status


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Řízení či oznámení je předběžně vyhodnoceno kladně.", "approved"),
        ("Řízení či oznámení se zpracovává se.", "processing"),
        ("Řízení či oznámení s číslem jednacím OAM-19391/ZM-2026 je v řízení.", "processing"),
        ("Pro zadané číslo jednací nebylo nalezeno žádné řízení.", "not_found"),
        ("Řízení či oznámení s číslem jednacím OAM-55488/ZM-2025 bylo nepovoleno.", "rejected"),
        ("Žádost byla zamítnuta.", "rejected"),
        ("Ministerstvo vnitra zamítlo vaši žádost nebo zastavilo řízení.", "rejected"),
        (
            "Proceedings or notification with reference number OAM-19391/ZM-2025 is being processed.",
            "processing",
        ),
        (
            "Proceedings or notification with reference number OAM-19391/ZM-2025 was preliminarily assessed positively.",
            "approved",
        ),
        ("Proceedings or notification with reference number OAM-55488/ZM-2025 have been closed.", "rejected"),
        ("The application was not found.", "not_found"),
        ("Neočekávaný výsledek bez známého stavu.", "rejected"),
    ],
)
def test_oamp_status_classifies_portal_messages(message, expected):
    assert _classify_result(message) == expected


def test_oamp_status_uses_english_portal_for_english_language():
    assert _status_url_for_language("en") == OAMP_STATUS_URL_EN
    assert _status_url_for_language("ru") == OAMP_STATUS_URL
    assert _status_url_for_language(None) == OAMP_STATUS_URL
