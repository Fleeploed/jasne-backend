"""Tests for app/services/help_search.py."""

from __future__ import annotations

import pytest

from app.models.help_schemas import (
    HelpCategory,
    HelpCategoryFallback,
    HelpCategoryTranslation,
    HelpGuide,
    HelpSectionText,
    HelpUiLocale,
    HelpUiStrings,
    QuickActionStrings,
    ScanCtaStrings,
    UsefulActionStrings,
)
from app.services.help_loader import HelpContent
from app.services.help_search import normalize, search


# ---------------------------------------------------------------------------
# Fixture factory
# ---------------------------------------------------------------------------


def _ui() -> HelpUiStrings:
    """Minimum legal HelpUiStrings — required by HelpContent shape but
    unused by the search logic."""
    locale = HelpUiLocale(
        quick_actions={"check_status": QuickActionStrings(title="x", subtitle="y")},
        scan_cta=ScanCtaStrings(title="x", subtitle="y", primary_button="z"),
        useful_actions={"scan_oamp_letter": UsefulActionStrings(title="x")},
    )
    return HelpUiStrings(translations={"cs": locale, "ru": locale})


def _category(slug: str, *, ru_title: str, ru_subtitle: str | None = None) -> HelpCategory:
    return HelpCategory(
        slug=slug,
        translations={
            "cs": HelpCategoryTranslation(title=f"cs-{slug}"),
            "ru": HelpCategoryTranslation(title=ru_title, subtitle=ru_subtitle),
        },
        fallback={
            "cs": HelpCategoryFallback(title="x", text="y"),
            "ru": HelpCategoryFallback(title="x", text="y"),
        },
    )


def _guide(
    slug: str,
    category: str,
    *,
    language: str = "ru",
    title: str,
    subtitle: str | None = None,
    short_summary: str | None = None,
    keywords: list[str] | None = None,
    section_body: str = "пустое тело",
) -> HelpGuide:
    return HelpGuide(
        slug=slug,
        category=category,
        language=language,  # type: ignore[arg-type]
        title=title,
        subtitle=subtitle,
        short_summary=short_summary,
        keywords=keywords or [],
        sections=[HelpSectionText(type="text", body=section_body)],
    )


def _content_with(*guides: HelpGuide, categories: list[HelpCategory] | None = None) -> HelpContent:
    cats = categories or [_category("visa-pobyt", ru_title="Виза и pobyt")]
    by_slug: dict[str, dict] = {}
    for g in guides:
        by_slug.setdefault(g.slug, {})[g.language] = g
    return HelpContent(
        categories=tuple(cats),
        guides_by_slug=by_slug,
        ui_strings=_ui(),
    )


# ---------------------------------------------------------------------------
# normalize()
# ---------------------------------------------------------------------------


def test_normalize_strips_diacritics_and_lowercases():
    assert normalize("Řízení") == "rizeni"
    assert normalize("Pobyt") == "pobyt"
    assert normalize("ŠČĚŘŽÝÁÍÉ") == "scerzyaie"


def test_normalize_handles_empty():
    assert normalize("") == ""
    assert normalize(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# search() — basic matching
# ---------------------------------------------------------------------------


def test_empty_query_returns_no_results():
    content = _content_with(_guide("g1", "visa-pobyt", title="Pobyt guide"))
    assert search(content, "", "ru") == []
    assert search(content, "   ", "ru") == []


def test_title_substring_match():
    content = _content_with(_guide("g1", "visa-pobyt", title="Как продлить pobyt"))
    results = search(content, "продлить", "ru")
    assert len(results) == 1
    assert results[0].slug == "g1"
    assert results[0].type == "guide"


def test_keyword_match():
    content = _content_with(
        _guide("g1", "visa-pobyt", title="Что-то другое", keywords=["OAMP", "pobyt"])
    )
    results = search(content, "oamp", "ru")
    assert len(results) == 1
    assert results[0].slug == "g1"


def test_diacritic_insensitive():
    """Search 'rizeni' should match 'řízení' in title."""
    content = _content_with(_guide("g1", "visa-pobyt", title="Stav řízení OAMP"))
    results = search(content, "rizeni", "ru")
    assert len(results) == 1
    assert results[0].slug == "g1"


def test_section_body_substring():
    content = _content_with(
        _guide("g1", "visa-pobyt", title="Title", section_body="frs.gov.cz очень полезен")
    )
    results = search(content, "frs", "ru")
    assert len(results) == 1


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_title_ranks_above_section_body():
    content = _content_with(
        _guide("g1", "visa-pobyt", title="Boring", section_body="OAMP в теле"),
        _guide("g2", "visa-pobyt", title="OAMP в заголовке"),
    )
    results = search(content, "oamp", "ru")
    assert [r.slug for r in results] == ["g2", "g1"]


def test_exact_title_ranks_above_substring():
    content = _content_with(
        _guide("g1", "visa-pobyt", title="OAMP полный"),
        _guide("g2", "visa-pobyt", title="OAMP"),
    )
    results = search(content, "oamp", "ru")
    assert results[0].slug == "g2"  # exact match wins


def test_keyword_ranks_above_subtitle():
    content = _content_with(
        _guide("g1", "visa-pobyt", title="X", subtitle="oamp в подзаголовке"),
        _guide("g2", "visa-pobyt", title="Y", keywords=["oamp"]),
    )
    results = search(content, "oamp", "ru")
    assert results[0].slug == "g2"


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


def test_categories_appear_in_results():
    content = _content_with(
        _guide("g1", "visa-pobyt", title="Other"),
        categories=[_category("visa-pobyt", ru_title="Виза и pobyt", ru_subtitle="OAMP, разрешения")],
    )
    results = search(content, "pobyt", "ru")
    # Should include both the guide (no match) and the category
    types = {r.type for r in results}
    assert "category" in types


def test_language_fallback_to_cs():
    """If guide isn't in ru, search falls back to cs."""
    cs_only = HelpGuide(
        slug="g1",
        category="visa-pobyt",
        language="cs",
        title="Pobyt v Česku",
        sections=[HelpSectionText(type="text", body="x")],
    )
    content = _content_with(cs_only)
    results = search(content, "pobyt", "ru")
    assert len(results) >= 1
    assert any(r.slug == "g1" for r in results)


def test_unpublished_guide_excluded():
    unpublished = HelpGuide(
        slug="g1",
        category="visa-pobyt",
        language="ru",
        published=False,
        title="Pobyt unpublished",
        sections=[HelpSectionText(type="text", body="x")],
    )
    content = _content_with(unpublished)
    results = search(content, "pobyt", "ru")
    guide_results = [r for r in results if r.type == "guide"]
    assert guide_results == []


def test_no_match_returns_empty():
    content = _content_with(_guide("g1", "visa-pobyt", title="Pobyt"))
    assert search(content, "definitelynotinthecorpus", "ru") == []


def test_results_capped_at_max():
    """20 result limit."""
    from app.services.help_search import MAX_RESULTS

    guides = [
        _guide(f"g{i}", "visa-pobyt", title=f"Pobyt {i}", keywords=["pobyt"])
        for i in range(30)
    ]
    content = _content_with(*guides)
    results = search(content, "pobyt", "ru")
    # 30 guides + 1 category that has pobyt in title = up to 31; capped at 20
    assert len(results) <= MAX_RESULTS
