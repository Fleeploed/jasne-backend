from __future__ import annotations

import re
import unicodedata
from contextlib import suppress
from datetime import UTC, datetime

from app.config import get_settings
from app.models.schemas import (
    OampStatusCheckRequest,
    OampStatusCheckResponse,
    OampStatusOutcome,
    OampStatusQueryKind,
)

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised only in misconfigured deploys
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None

OAMP_STATUS_URL = "https://ipc.gov.cz/informace-o-stavu-rizeni/"
OAMP_STATUS_URL_EN = "https://ipc.gov.cz/en/status-of-your-application/"
OAMP_DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
OAMP_FAST_IDLE_WAIT_MS = 2500
_RESULT_RE = re.compile(
    r"((?:Řízení či oznámení s číslem jednacím|An application or report,\s*reference number|"
    r"Proceedings or notification with reference number).*?)"
    r"(?=Zadejte číslo jednací|Enter reference number|Enter the reference number|$)",
    re.IGNORECASE,
)
_KEYWORD_RESULT_RE = re.compile(
    r"([^.!?]*(?:kladn|zpracováv\w*\s+se|zpracovav\w*\s+se|nebylo\s+nalezeno|"
    r"nenalezen|nepovol|zamítn|zamit|nevyhov|zastavil|preliminarily\s+assessed\s+positively|"
    r"being\s+processed|have\s+been\s+closed|not\s+found)[^.!?]*(?:[.!?]|$))",
    re.IGNORECASE,
)


class OampStatusError(RuntimeError):
    """Base class for OAMP lookup failures."""


class OampStatusTimeoutError(OampStatusError):
    """The IPC/OAMP page did not respond or render in time."""


class OampStatusUnsupportedError(OampStatusError):
    """The request kind is accepted by the API but not wired to IPC selectors yet."""


def check_oamp_status(
    payload: OampStatusCheckRequest,
    *,
    user_agent: str | None = None,
) -> OampStatusCheckResponse:
    """Check OAMP proceedings or visa application status through the public IPC form.

    This is intentionally synchronous because Playwright's sync API is stable
    and mirrors the working local script. The FastAPI router runs it in a
    worker thread so it does not block the event loop.
    """

    query_kind = _query_kind(payload)
    query_id = _format_query_id(payload, query_kind)

    if sync_playwright is None:
        raise OampStatusError("Playwright is not installed")

    settings = get_settings()
    source_url = _status_url_for_language(payload.language)
    timeout_ms = int(settings.oamp_timeout_seconds * 1000)

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
                    user_agent=user_agent or OAMP_DEFAULT_USER_AGENT,
                )
                page.goto(source_url, wait_until="domcontentloaded", timeout=timeout_ms)

                with suppress(PlaywrightTimeoutError):
                    page.wait_for_load_state(
                        "networkidle",
                        timeout=min(timeout_ms, OAMP_FAST_IDLE_WAIT_MS),
                    )

                if query_kind == "proceedings":
                    page.locator('input[name="proceedings.referenceNumber"]').wait_for(timeout=timeout_ms)
                    _fill_input(page, "proceedings.referenceNumber", payload.reference_number or "")

                    if payload.additional_suffix:
                        _fill_input(page, "proceedings.additionalSuffix", payload.additional_suffix)

                    _select_react_select(page, "proceedings.category", payload.category or "")
                    _select_react_select(page, "proceedings.year", payload.year or "")
                else:
                    page.locator('input[name="visaApplicationNumber"]').wait_for(timeout=timeout_ms)
                    _fill_input(page, "visaApplicationNumber", payload.visa_number or "")

                page.locator('button[type="submit"]').click(timeout=10000)

                with suppress(PlaywrightTimeoutError):
                    page.wait_for_load_state(
                        "networkidle",
                        timeout=min(timeout_ms, OAMP_FAST_IDLE_WAIT_MS),
                    )

                _wait_for_result_text(page, timeout_ms=timeout_ms)
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
        kind=query_kind,
        query=query_id,
        status=_classify_result(message),
        message=message,
        source_url=source_url,
        checked_at=datetime.now(UTC),
    )


def _query_kind(payload: OampStatusCheckRequest) -> OampStatusQueryKind:
    if _has_proceedings_fields(payload):
        return "proceedings"
    if payload.visa_number:
        return "visa"
    raise OampStatusUnsupportedError("Send either OAMP proceedings fields or visa_number")


def _has_proceedings_fields(payload: OampStatusCheckRequest) -> bool:
    return bool(payload.reference_number and payload.category and payload.year)


def _format_case_id(payload: OampStatusCheckRequest) -> str:
    if not _has_proceedings_fields(payload):
        raise OampStatusUnsupportedError("OAMP proceedings fields are required")

    optional = f"-{payload.additional_suffix}" if payload.additional_suffix else ""
    return f"OAM-{payload.reference_number}{optional}/{payload.category}-{payload.year}"


def _format_query_id(payload: OampStatusCheckRequest, query_kind: OampStatusQueryKind) -> str:
    if query_kind == "visa":
        if not payload.visa_number:
            raise OampStatusUnsupportedError("visa_number is required")
        return payload.visa_number
    return _format_case_id(payload)


def _status_url_for_language(language: str | None) -> str:
    settings = get_settings()
    path = settings.oamp_status_path_en if language == "en" else settings.oamp_status_path
    return f"{settings.oamp_base_url.rstrip('/')}/{path.lstrip('/')}"


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _normalize_for_match(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _normalize_text(without_accents).lower()


def _fill_input(page, name: str, value: str) -> None:
    page.locator(f'input[name="{name}"]').fill(value, timeout=10000)


def _select_react_select(page, hidden_input_name: str, value: str) -> None:
    hidden_input = page.locator(f'input[name="{hidden_input_name}"]')
    wrapper = hidden_input.locator("xpath=ancestor::div[contains(@class, 'select__wrapper')]")
    control = wrapper.locator(".react-select__control")

    control.click(timeout=10000)
    page.get_by_text(value, exact=True).click(timeout=10000)


def _wait_for_result_text(page, timeout_ms: int) -> None:
    page.wait_for_function(
        """
        () => {
          const text = document.body?.innerText || "";
          return /Řízení či oznámení s číslem jednacím|An application or report,\\s*reference number|Proceedings or notification with reference number|nebylo nalezeno|not found/i.test(text);
        }
        """,
        timeout=timeout_ms,
    )


def _extract_result(page) -> str:
    body = _normalize_text(page.locator("body").inner_text(timeout=10000))
    match = _RESULT_RE.search(body)
    if match:
        return _normalize_text(match.group(1))
    keyword_match = _KEYWORD_RESULT_RE.search(body)
    if keyword_match:
        return _normalize_text(keyword_match.group(1))
    raise OampStatusError("Could not find OAMP result block")


def _classify_result(message: str) -> OampStatusOutcome:
    normalized = _normalize_for_match(message)
    if (
        "nebylo nalezeno" in normalized
        or "nebyl" in normalized
        or "nenalezen" in normalized
        or "not found" in normalized
    ):
        return "not_found"
    if "kladn" in normalized or "preliminarily assessed positively" in normalized:
        return "approved"
    if (
        "nepovol" in normalized
        or "zamit" in normalized
        or "nevyhov" in normalized
        or "zastavil" in normalized
        or "rejected" in normalized
        or "have been closed" in normalized
    ):
        return "rejected"
    if "zpracovava se" in normalized or "v rizeni" in normalized or "being processed" in normalized:
        return "processing"
    return "rejected"
