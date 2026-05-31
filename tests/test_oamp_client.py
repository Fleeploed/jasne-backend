from datetime import UTC, datetime

import pytest

from app.models.schemas import OampStatusCheckResponse
from app.services.oamp_client import check_status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Řízení či oznámení se zpracovává se.", "probiha"),
        ("Řízení bylo předběžně vyhodnoceno kladně.", "schvaleno"),
        ("Řízení či oznámení s číslem jednacím bylo nepovoleno.", "zamitnuto"),
        ("Pro zadané číslo jednací nebylo nalezeno žádné řízení.", "not_found"),
        ("Neočekávaný výsledek bez známého stavu.", "error"),
    ],
)
async def test_check_status_maps_oamp_text(monkeypatch, message, expected):
    def fake_check(_payload, *, user_agent=None):
        return OampStatusCheckResponse(
            kind="proceedings",
            query="OAM-19391/ZM-2026",
            status="unknown",
            message=message,
            source_url="https://ipc.gov.cz/informace-o-stavu-rizeni/",
            checked_at=datetime.now(UTC),
        )

    monkeypatch.setattr("app.services.oamp_client.check_oamp_status", fake_check)

    assert await check_status("OAM-19391/ZM-2026") == expected


@pytest.mark.asyncio
async def test_check_status_returns_error_on_invalid_case():
    assert await check_status("not-a-case") == "error"
