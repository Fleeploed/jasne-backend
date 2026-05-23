from __future__ import annotations

import re
from contextlib import suppress
from datetime import UTC, datetime

from app.models.schemas import OampStatusCheckRequest, OampStatusCheckResponse

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised only in misconfigured deploys
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None

OAMP_STATUS_URL = "https://ipc.gov.cz/informace-o-stavu-rizeni/"
_RESULT_RE = re.compile(
    r"(Řízení či oznámení s číslem jednacím .*?)(?=Zadejte číslo jednací|$)",
    re.IGNORECASE,
)


class OampStatusError(RuntimeError):
    """Base class for OAMP lookup failures."""


class OampStatusTimeoutError(OampStatusError):
    """The IPC/OAMP page did not respond or render in time."""


class OampStatusUnsupportedError(OampStatusError):
    """The request kind is accepted by the API but not wired to IPC selectors yet."""


def check_oamp_status(payload: OampStatusCheckRequest) -> OampStatusCheckResponse:
    """Check OAMP proceedings status through the public IPC form.

    This is intentionally synchronous because Playwright's sync API is stable
    and mirrors the working local script. The FastAPI router runs it in a
    worker thread so it does not block the event loop.
    """

    if payload.visa_number and not _has_proceedings_fields(payload):
        raise OampStatusUnsupportedError("ŽOV lookup is not wired yet; send OAMP proceedings fields")

    case_id = _format_case_id(payload)

    if sync_playwright is None:
        raise OampStatusError("Playwright is not installed")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )

            try:
                page = browser.new_page(
                    viewport={"width": 1400, "height": 1000},
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page.goto(OAMP_STATUS_URL, wait_until="domcontentloaded", timeout=60000)

                with suppress(PlaywrightTimeoutError):
                    page.wait_for_load_state("networkidle", timeout=30000)

                page.locator('input[name="proceedings.referenceNumber"]').wait_for(timeout=30000)
                _fill_input(page, "proceedings.referenceNumber", payload.reference_number or "")

                if payload.additional_suffix:
                    _fill_input(page, "proceedings.additionalSuffix", payload.additional_suffix)

                _select_react_select(page, "proceedings.category", payload.category or "")
                _select_react_select(page, "proceedings.year", payload.year or "")

                page.locator('button[type="submit"]').click(timeout=10000)

                with suppress(PlaywrightTimeoutError):
                    page.wait_for_load_state("networkidle", timeout=30000)

                page.wait_for_timeout(3000)
                message = _extract_result(page)
            finally:
                browser.close()

    except PlaywrightTimeoutError as exc:
        raise OampStatusTimeoutError("Timed out while checking OAMP status") from exc
    except OampStatusError:
        raise
    except Exception as exc:
        raise OampStatusError(f"Could not check OAMP status: {type(exc).__name__}") from exc

    return OampStatusCheckResponse(
        kind="proceedings",
        query=case_id,
        status=_classify_result(message),
        message=message,
        source_url=OAMP_STATUS_URL,
        checked_at=datetime.now(UTC),
    )


def _has_proceedings_fields(payload: OampStatusCheckRequest) -> bool:
    return bool(payload.reference_number and payload.category and payload.year)


def _format_case_id(payload: OampStatusCheckRequest) -> str:
    if not _has_proceedings_fields(payload):
        raise OampStatusUnsupportedError("OAMP proceedings fields are required")

    optional = f"-{payload.additional_suffix}" if payload.additional_suffix else ""
    return f"OAM-{payload.reference_number}{optional}/{payload.category}-{payload.year}"


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _fill_input(page, name: str, value: str) -> None:
    page.locator(f'input[name="{name}"]').fill(value, timeout=10000)


def _select_react_select(page, hidden_input_name: str, value: str) -> None:
    hidden_input = page.locator(f'input[name="{hidden_input_name}"]')
    wrapper = hidden_input.locator("xpath=ancestor::div[contains(@class, 'select__wrapper')]")
    control = wrapper.locator(".react-select__control")

    control.click(timeout=10000)
    page.get_by_text(value, exact=True).click(timeout=10000)


def _extract_result(page) -> str:
    body = _normalize_text(page.locator("body").inner_text(timeout=10000))
    match = _RESULT_RE.search(body)
    if match:
        return _normalize_text(match.group(1))
    raise OampStatusError("Could not find OAMP result block")


def _classify_result(message: str):
    lower = message.lower()
    if "nebyl" in lower or "nenalezen" in lower or "nenalezeno" in lower:
        return "not_found"
    if "řízení či oznámení s číslem jednacím" in lower:
        return "found"
    return "unknown"
