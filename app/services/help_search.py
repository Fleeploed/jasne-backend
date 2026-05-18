"""Help-section search.

Pure-Python ranked substring matching over the in-memory `HelpContent`.
Works because we expect ≤ a few hundred guides; revisit when scaling past
that. Diacritic-insensitive ("rizeni" matches "řízení").
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

from app.models.help_schemas import (
    HelpCategory,
    HelpGuide,
    HelpLanguage,
    HelpSearchResult,
)
from app.services.help_loader import HelpContent

MAX_RESULTS = 20


def normalize(value: str) -> str:
    """Lowercase + strip diacritics. Used on both query and corpus."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def search(content: HelpContent, query: str, language: HelpLanguage) -> list[HelpSearchResult]:
    """Score guides + categories against the query, return top MAX_RESULTS."""
    q = normalize(query.strip())
    if not q:
        return []

    scored: list[tuple[int, int, HelpSearchResult]] = []
    # tiebreaker_index sorts ties by category appearance order (stable rank).

    for guide_idx, guide in enumerate(_guides_for_language(content, language)):
        score = _score_guide(guide, q)
        if score == 0:
            continue
        category = content.find_category(guide.category)
        category_title = (
            category.translations[language].title
            if category and language in category.translations
            else (category.translations["cs"].title if category else None)
        )
        scored.append(
            (
                -score,
                guide_idx,
                HelpSearchResult(
                    type="guide",
                    slug=guide.slug,
                    title=guide.title,
                    subtitle=guide.subtitle,
                    category_title=category_title,
                ),
            )
        )

    for cat_idx, category in enumerate(content.categories):
        score = _score_category(category, q, language)
        if score == 0:
            continue
        translation = category.translations.get(language) or category.translations["cs"]
        scored.append(
            (
                -score,
                cat_idx,
                HelpSearchResult(
                    type="category",
                    slug=category.slug,
                    title=translation.title,
                    subtitle=translation.subtitle,
                ),
            )
        )

    scored.sort(key=lambda triple: (triple[0], triple[1]))
    return [result for _, _, result in scored[:MAX_RESULTS]]


def _guides_for_language(content: HelpContent, language: HelpLanguage) -> Iterable[HelpGuide]:
    """Yield published guides in the requested language, falling back to cs."""
    for langs in content.guides_by_slug.values():
        guide = langs.get(language) or langs.get("cs")
        if guide and guide.published:
            yield guide


def _score_guide(guide: HelpGuide, q: str) -> int:
    title_n = normalize(guide.title)
    if title_n == q:
        return 100

    keywords_n = [normalize(k) for k in guide.keywords]
    if q in keywords_n:
        return 80

    if q in title_n:
        return 60

    subtitle_n = normalize(guide.subtitle or "")
    summary_n = normalize(guide.short_summary or "")
    if q in subtitle_n or q in summary_n:
        return 40

    for section in guide.sections:
        if _section_text_match(section, q):
            return 20
    return 0


def _section_text_match(section, q: str) -> bool:
    """Any text/title/item field that contains the query substring."""
    title = getattr(section, "title", None)
    if title and q in normalize(title):
        return True

    body = getattr(section, "body", None)
    if body and q in normalize(body):
        return True

    items: list[str] | None = getattr(section, "items", None)
    if items:
        for item in items:
            if q in normalize(item):
                return True
    return False


def _score_category(category: HelpCategory, q: str, language: HelpLanguage) -> int:
    translation = category.translations.get(language) or category.translations["cs"]
    title_n = normalize(translation.title)
    subtitle_n = normalize(translation.subtitle or "")
    if q in title_n or q in subtitle_n:
        return 50
    return 0
