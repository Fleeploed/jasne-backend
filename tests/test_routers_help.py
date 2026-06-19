"""Integration tests for /help/* endpoints.

The FastAPI lifespan runs `load_help_content()` against the real
`backend/help_content/` directory, so these tests assert against the seed
content shipped with the MVP (6 categories: visa-pobyt, taxes, bills,
contracts, urad-prace, health-insurance; 4 guides, all in ru).

For edge cases that the seed doesn't cover, individual tests patch
`get_help_content()` with a hand-built `HelpContent`.
"""

from __future__ import annotations

import pytest

from app.models.help_schemas import (
    HelpCategory,
    HelpCategoryFallback,
    HelpCategoryTranslation,
    HelpGuide,
    HelpSectionChecklist,
    HelpSectionSteps,
    HelpSectionText,
    HelpSectionWarning,
    HelpUiLocale,
    HelpUiStrings,
    QuickActionStrings,
    ScanCtaStrings,
    UsefulActionStrings,
)
from app.services.help_loader import HelpContent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_ui() -> HelpUiStrings:
    locale = HelpUiLocale(
        quick_actions={
            "check_status": QuickActionStrings(title="QA", subtitle="qa"),
            "prepare_documents": QuickActionStrings(title="PD", subtitle="pd"),
            "understand_letter": QuickActionStrings(title="UL", subtitle="ul"),
        },
        scan_cta=ScanCtaStrings(
            title="SCT", subtitle="sct", primary_button="scan", helper_text=None, pro_hint=None
        ),
        useful_actions={
            "scan_oamp_letter": UsefulActionStrings(title="Scan OAMP", subtitle=None),
            "add_deadline_manual": UsefulActionStrings(title="Add Deadline", subtitle=None),
            "open_deadlines": UsefulActionStrings(title="Open Deadlines", subtitle=None),
        },
    )
    return HelpUiStrings(translations={"cs": locale, "ru": locale, "en": locale, "uk": locale})


def _build_test_content() -> HelpContent:
    """Tiny hand-built content for tests that need control over the data."""
    cat_visa = HelpCategory(
        slug="visa-pobyt",
        icon="passport",
        translations={
            "cs": HelpCategoryTranslation(title="Víza a pobyt"),
            "ru": HelpCategoryTranslation(title="Визы и проживание", subtitle="МВД Чехии"),
        },
        fallback={
            "cs": HelpCategoryFallback(title="Prázdné cs", text="cs text"),
            "ru": HelpCategoryFallback(title="Пусто ru", text="ru text"),
        },
        guides_order=["check-status"],
    )
    cat_health = HelpCategory(
        slug="health-insurance",
        icon="shield",
        translations={
            "cs": HelpCategoryTranslation(title="Zdravotní pojištění"),
            "ru": HelpCategoryTranslation(title="Медстраховка"),
        },
        fallback={
            "cs": HelpCategoryFallback(title="Prázdné", text="bude"),
            "ru": HelpCategoryFallback(title="Раздел в работе", text="скоро"),
        },
    )

    guide_ru = HelpGuide(
        slug="check-status",
        category="visa-pobyt",
        language="ru",
        title="Как проверить статус заявления",
        subtitle="OAMP",
        sections=[HelpSectionText(type="text", body="OAMP, stav řízení.")],
    )
    guide_cs = HelpGuide(
        slug="check-status",
        category="visa-pobyt",
        language="cs",
        title="Jak zkontrolovat stav řízení",
        sections=[HelpSectionText(type="text", body="OAMP, stav řízení.")],
    )

    return HelpContent(
        categories=(cat_visa, cat_health),
        guides_by_slug={"check-status": {"ru": guide_ru, "cs": guide_cs}},
        ui_strings=_minimal_ui(),
    )


@pytest.fixture(autouse=True)
def default_snapshot_from_yaml(monkeypatch):
    """Migration 0011 moved guides to Postgres. By default, route tests still
    rely on the YAML-seeded content, so we patch `_snapshot()` to return what
    the YAML loader produced (same shape as before the cutover). Tests that
    need a hand-built HelpContent use `patched_content` which overrides this.
    """
    from app.services.help_loader import get_help_content as _real_yaml_content

    async def _yaml_snapshot():
        return _real_yaml_content()

    monkeypatch.setattr("app.routers.help._snapshot", _yaml_snapshot)


@pytest.fixture
def patched_content(monkeypatch):
    """Tests use this to inject controlled HelpContent rather than seed YAML."""
    content = _build_test_content()

    async def _fake_snapshot():
        return content

    monkeypatch.setattr("app.routers.help.get_help_content", lambda: content)
    monkeypatch.setattr("app.routers.help._snapshot", _fake_snapshot)
    return content


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_help_home_requires_auth(client):
    r = client.get("/help/home")
    assert r.status_code in (401, 403)  # depends on HTTPBearer configuration


def test_help_search_requires_auth(client):
    r = client.get("/help/search?q=oamp")
    assert r.status_code in (401, 403)


def test_help_category_requires_auth(client):
    r = client.get("/help/categories/visa-pobyt")
    assert r.status_code in (401, 403)


def test_help_guide_requires_auth(client):
    r = client.get("/help/guides/check-status")
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# /help/home
# ---------------------------------------------------------------------------


def test_home_returns_quick_actions_and_categories(client, auth_headers):
    r = client.get("/help/home?language=ru", headers=auth_headers)
    assert r.status_code == 200, r.text

    body = r.json()
    assert "quick_actions" in body
    assert "popular_categories" in body
    assert "scan_cta" in body

    # Current help home keeps only the two primary quick actions.
    keys = {a["key"] for a in body["quick_actions"]}
    assert keys == {"check_status", "understand_letter"}

    # visa-pobyt should be first in popular order
    assert body["popular_categories"][0]["slug"] == "visa-pobyt"
    # All 6 seed categories present
    assert len(body["popular_categories"]) == 6

    # scan_cta has primary_action wired to scan_new
    assert body["scan_cta"]["primary_action"] == "scan_new"
    assert body["scan_cta"]["primary_button"]  # non-empty


def test_home_default_language_is_cs(client, auth_headers):
    r = client.get("/help/home", headers=auth_headers)
    assert r.status_code == 200
    # cs categories use Czech titles
    assert any(c["title"] == "Víza a pobyt" for c in r.json()["popular_categories"])


def test_home_invalid_language_returns_422(client, auth_headers):
    r = client.get("/help/home?language=fr", headers=auth_headers)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# /help/categories/{slug}
# ---------------------------------------------------------------------------


def test_category_returns_guides(client, auth_headers):
    r = client.get("/help/categories/visa-pobyt?language=ru", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "visa-pobyt"
    assert body["title"] == "Визы и проживание"
    assert body["fallback"] is None
    # Seed includes check-status + extend-pobyt
    guide_slugs = {g["slug"] for g in body["guides"]}
    assert "check-status" in guide_slugs
    assert "extend-pobyt" in guide_slugs
    # First by author order
    assert body["guides"][0]["slug"] == "check-status"
    # useful_actions populated from defaults
    assert len(body["useful_actions"]) >= 1


def test_category_falls_back_to_seed_guide_language(client, auth_headers):
    r = client.get("/help/categories/visa-pobyt?language=cs", headers=auth_headers)
    assert r.status_code == 200, r.text
    guide_slugs = {g["slug"] for g in r.json()["guides"]}
    assert "check-status" in guide_slugs


def test_category_with_no_guides_returns_fallback(client, auth_headers):
    """health-insurance has no guides in the MVP seed."""
    r = client.get("/help/categories/health-insurance?language=ru", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["fallback"] is not None
    assert body["fallback"]["title"]  # non-empty
    assert body["guides"] == []


def test_category_not_found(client, auth_headers):
    r = client.get("/help/categories/totally-fake", headers=auth_headers)
    assert r.status_code == 404


def test_category_hero_populated(client, auth_headers):
    r = client.get("/help/categories/visa-pobyt?language=ru", headers=auth_headers)
    body = r.json()
    assert body["hero"]["title"]
    assert body["hero"]["text"]


# ---------------------------------------------------------------------------
# /help/guides/{slug}
# ---------------------------------------------------------------------------


def test_guide_returns_full_content(client, auth_headers):
    r = client.get("/help/guides/check-status?language=ru", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "check-status"
    assert body["language"] == "ru"
    assert body["fallback_language"] is None
    assert body["category"]["slug"] == "visa-pobyt"
    # Sections from the seed YAML
    assert len(body["sections"]) >= 1
    assert any(s["type"] == "text" for s in body["sections"])
    assert any(s["type"] == "warning" for s in body["sections"])
    assert all("pro_only" in s for s in body["sections"])
    assert any(s["type"] == "checklist" and s["pro_only"] is True for s in body["sections"])


def test_guide_returns_seed_czech_content(client, auth_headers):
    r = client.get("/help/guides/check-status?language=cs", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["language"] == "cs"
    assert body["fallback_language"] is None


def test_guide_missing_requested_language_returns_404(client, auth_headers, patched_content):
    r = client.get("/help/guides/check-status?language=en", headers=auth_headers)
    assert r.status_code == 404


def test_guide_request_cs_no_fallback_flag(client, auth_headers, patched_content):
    """When user asks for cs and we return cs, fallback_language is null."""
    r = client.get("/help/guides/check-status?language=cs", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["language"] == "cs"
    assert body["fallback_language"] is None


def test_guide_not_found(client, auth_headers):
    r = client.get("/help/guides/totally-fake", headers=auth_headers)
    assert r.status_code == 404


def test_guide_invalid_language_returns_422(client, auth_headers):
    r = client.get("/help/guides/check-status?language=fr", headers=auth_headers)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# /help/guides/{slug} — Pro gating (free users get a teaser, the bulk stays in)
# ---------------------------------------------------------------------------

# Free users receive a short teaser (first item) of each pro_only section. The
# teaser markers MAY appear; the deep markers (later items) MUST NOT — they
# prove the bulk of the Pro payload never crosses the API boundary.
_PRO_STEP_TEASER = "FIRST_STEP_TEASER_OK"
_PRO_STEP_SECRET = "SECRET_SECOND_STEP_DO_NOT_LEAK"
_PRO_CHECK_TEASER = "FIRST_CHECK_TEASER_OK"
_PRO_CHECK_SECRET = "SECRET_SECOND_CHECK_DO_NOT_LEAK"
# Public + warning markers MUST stay visible to free users.
_PUBLIC_MARKER = "PUBLIC_INTRO_TEXT"
_WARNING_MARKER = "WARNING_ALWAYS_VISIBLE"


def _content_with_pro_section() -> HelpContent:
    cat_visa = HelpCategory(
        slug="visa-pobyt",
        icon="passport",
        translations={
            "cs": HelpCategoryTranslation(title="Víza a pobyt"),
            "ru": HelpCategoryTranslation(title="Визы и проживание"),
        },
        fallback={
            "cs": HelpCategoryFallback(title="Prázdné", text="cs"),
            "ru": HelpCategoryFallback(title="Пусто", text="ru"),
        },
        guides_order=["check-status"],
    )
    guide_ru = HelpGuide(
        slug="check-status",
        category="visa-pobyt",
        language="ru",
        title="Как проверить статус",
        sections=[
            HelpSectionText(type="text", title="Коротко", body=_PUBLIC_MARKER),
            HelpSectionSteps(
                type="steps",
                title="Что сделать",
                pro_only=True,
                items=[_PRO_STEP_TEASER, _PRO_STEP_SECRET],
            ),
            HelpSectionChecklist(
                type="checklist",
                title="Что подготовить",
                pro_only=True,
                items=[_PRO_CHECK_TEASER, _PRO_CHECK_SECRET],
            ),
            HelpSectionWarning(type="warning", title="Важно", pro_only=True, body=_WARNING_MARKER),
        ],
    )
    return HelpContent(
        categories=(cat_visa,),
        guides_by_slug={"check-status": {"ru": guide_ru}},
        ui_strings=_minimal_ui(),
    )


@pytest.fixture
def pro_section_content(monkeypatch):
    content = _content_with_pro_section()

    async def _fake_snapshot():
        return content

    monkeypatch.setattr("app.routers.help.get_help_content", lambda: content)
    monkeypatch.setattr("app.routers.help._snapshot", _fake_snapshot)
    return content


def _seed_plan(fake_supabase, test_user_id, plan: str) -> None:
    fake_supabase.table("profiles").rows.append({"id": str(test_user_id), "plan": plan})


def test_guide_previews_pro_only_content_for_free_user(
    client, auth_headers, fake_supabase, test_user_id, pro_section_content
):
    _seed_plan(fake_supabase, test_user_id, "free")

    r = client.get("/help/guides/check-status?language=ru", headers=auth_headers)
    assert r.status_code == 200, r.text

    # Only the first-item teaser is exposed; the bulk of the Pro payload (later
    # items) must be absent from the entire serialized response. The teaser is a
    # marketing preview, not the security boundary — the rest stays server-side.
    assert _PRO_STEP_SECRET not in r.text
    assert _PRO_CHECK_SECRET not in r.text

    # Public text and the always-open warning stay fully readable.
    assert _PUBLIC_MARKER in r.text
    assert _WARNING_MARKER in r.text

    by_type = {s["type"]: s for s in r.json()["sections"]}
    # Gated sections keep their shell + a single-item teaser, flagged locked.
    assert by_type["steps"]["pro_only"] is True
    assert by_type["steps"]["locked"] is True
    assert by_type["steps"]["items"] == [_PRO_STEP_TEASER]
    assert by_type["steps"]["title"] == "Что сделать"
    assert by_type["checklist"]["locked"] is True
    assert by_type["checklist"]["items"] == [_PRO_CHECK_TEASER]
    # Warning is never gated — full body survives, not locked.
    assert by_type["warning"]["locked"] is False
    assert by_type["warning"]["body"] == _WARNING_MARKER


def test_guide_returns_pro_only_content_for_pro_user(
    client, auth_headers, fake_supabase, test_user_id, pro_section_content
):
    _seed_plan(fake_supabase, test_user_id, "pro")

    r = client.get("/help/guides/check-status?language=ru", headers=auth_headers)
    assert r.status_code == 200, r.text

    # Pro users receive the untouched payload, including the deep items.
    assert _PRO_STEP_SECRET in r.text
    assert _PRO_CHECK_SECRET in r.text

    by_type = {s["type"]: s for s in r.json()["sections"]}
    assert by_type["steps"]["items"] == [_PRO_STEP_TEASER, _PRO_STEP_SECRET]
    assert by_type["steps"]["locked"] is False
    assert by_type["checklist"]["items"] == [_PRO_CHECK_TEASER, _PRO_CHECK_SECRET]
    assert by_type["checklist"]["locked"] is False


# ---------------------------------------------------------------------------
# /help/search
# ---------------------------------------------------------------------------


def test_search_finds_guide(client, auth_headers):
    r = client.get("/help/search?q=OAMP&language=ru", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "OAMP"
    assert body["results_count"] >= 1
    # check-status guide has OAMP in title/keywords
    guide_slugs = [r["slug"] for r in body["results"] if r["type"] == "guide"]
    assert "check-status" in guide_slugs


def test_search_diacritic_insensitive(client, auth_headers):
    r = client.get("/help/search?q=rizeni&language=ru", headers=auth_headers)
    assert r.status_code == 200
    # "stav řízení" appears in check-status guide
    assert r.json()["results_count"] >= 1


def test_search_empty_returns_empty_results(client, auth_headers):
    r = client.get("/help/search?q=&language=ru", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["results_count"] == 0
    assert body["results"] == []


def test_search_no_matches_returns_zero(client, auth_headers):
    r = client.get("/help/search?q=definitelynothingmatchesthis&language=ru", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["results_count"] == 0
