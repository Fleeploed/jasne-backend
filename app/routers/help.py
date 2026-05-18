"""GET /help/* — read-only content endpoints for the Help tab.

All four endpoints require auth (Supabase JWT). Help is signed-in only;
guest mode uses the onboarding flow.

Content comes from the in-memory `HelpContent` singleton populated by
`load_help_content()` during app startup — no DB tables, no per-request
filesystem reads.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.middleware.auth import get_current_user_id
from app.models.help_schemas import (
    HelpCategoryHero,
    HelpCategoryResponse,
    HelpFallbackBlock,
    HelpGuide,
    HelpGuideCard,
    HelpGuideCategoryRef,
    HelpGuideResponse,
    HelpHomeResponse,
    HelpLanguage,
    HelpPopularCategory,
    HelpQuickAction,
    HelpScanCta,
    HelpSearchResponse,
    HelpUsefulAction,
)
from app.services.help_loader import HelpContent, get_help_content
from app.services.help_search import search as run_search

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/help", tags=["help"])

# Hardcoded ordering of UI sections. The labels for these keys live in
# help_ui_strings.yaml. Adding a new key here also requires adding the
# strings entry in every locale; the loader validates the cs entry.

_QUICK_ACTION_ROUTES: dict[str, str] = {
    "check_status": "/help/guides/check-status",
    "prepare_documents": "/help/categories/visa-pobyt",
    "understand_letter": "/scan/new",
}

# Default useful_actions when a category does not override.
_DEFAULT_USEFUL_ACTIONS: list[dict[str, object]] = [
    {"key": "scan_oamp_letter", "action": "scan_new", "highlighted": True},
    {"key": "add_deadline_manual", "action": "deadline_new", "highlighted": False},
    {"key": "open_deadlines", "action": "deadlines_tab", "highlighted": False},
]

# Order in which categories appear in the Home screen "popular" carousel.
# Falls back to file order if not listed here.
_POPULAR_CATEGORY_ORDER: tuple[str, ...] = ("visa-pobyt", "taxes", "bills", "urad-prace")
_GUIDE_FALLBACK_ORDER: tuple[HelpLanguage, ...] = ("cs", "ru", "en", "uk")


# ---------------------------------------------------------------------------
# /help/home
# ---------------------------------------------------------------------------


@router.get("/home", response_model=HelpHomeResponse)
async def help_home(
    language: HelpLanguage = Query("cs"),
    _user_id: UUID = Depends(get_current_user_id),
) -> HelpHomeResponse:
    content = get_help_content()
    ui = _resolve_ui(content, language)

    quick_actions: list[HelpQuickAction] = []
    for key, route in _QUICK_ACTION_ROUTES.items():
        strings = ui.quick_actions.get(key)
        if strings is None:
            # Missing translation for this key — skip, do not crash.
            logger.warning("Quick action '%s' has no string for language=%s", key, language)
            continue
        quick_actions.append(
            HelpQuickAction(key=key, title=strings.title, subtitle=strings.subtitle, route=route)
        )

    popular = _popular_categories(content, language)

    return HelpHomeResponse(
        quick_actions=quick_actions,
        popular_categories=popular,
        scan_cta=HelpScanCta(
            title=ui.scan_cta.title,
            subtitle=ui.scan_cta.subtitle,
            primary_button=ui.scan_cta.primary_button,
            primary_action="scan_new",
            helper_text=ui.scan_cta.helper_text,
            pro_hint=ui.scan_cta.pro_hint,
        ),
    )


# ---------------------------------------------------------------------------
# /help/categories/{slug}
# ---------------------------------------------------------------------------


@router.get("/categories/{slug}", response_model=HelpCategoryResponse)
async def help_category(
    slug: str,
    language: HelpLanguage = Query("cs"),
    _user_id: UUID = Depends(get_current_user_id),
) -> HelpCategoryResponse:
    content = get_help_content()
    category = content.find_category(slug)
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    translation = category.translations.get(language) or category.translations["cs"]
    fallback_translation = category.fallback.get(language) or category.fallback["cs"]

    guides_in_language = _published_guides_with_fallback(content, slug, language)
    has_any_published = bool(guides_in_language)
    is_completely_empty = not has_any_published

    ordered_cards = _ordered_guide_cards(category.guides_order, guides_in_language)

    return HelpCategoryResponse(
        slug=slug,
        title=translation.title,
        subtitle=translation.subtitle,
        hero=HelpCategoryHero(
            title=translation.hero_title,
            text=translation.hero_text,
            badge=translation.badge,
        ),
        guides=ordered_cards,
        fallback=(
            HelpFallbackBlock(title=fallback_translation.title, text=fallback_translation.text)
            if is_completely_empty
            else None
        ),
        useful_actions=_resolve_useful_actions(content, category.useful_actions, language),
    )


# ---------------------------------------------------------------------------
# /help/guides/{slug}
# ---------------------------------------------------------------------------


@router.get("/guides/{slug}", response_model=HelpGuideResponse)
async def help_guide(
    slug: str,
    language: HelpLanguage = Query("cs"),
    _user_id: UUID = Depends(get_current_user_id),
) -> HelpGuideResponse:
    content = get_help_content()
    langs = content.guides_by_slug.get(slug)
    if not langs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guide not found")

    guide = _published_guide_with_fallback(langs, language)
    if guide is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guide not available")
    fallback_language: HelpLanguage | None = guide.language if guide.language != language else None

    category = content.find_category(guide.category)
    if category is None:
        # Should never happen — loader validates this. Defensive 500.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Guide references unknown category",
        )

    cat_translation = category.translations.get(language) or category.translations["cs"]

    return HelpGuideResponse(
        slug=guide.slug,
        category=HelpGuideCategoryRef(slug=category.slug, title=cat_translation.title),
        title=guide.title,
        subtitle=guide.subtitle,
        short_summary=guide.short_summary,
        language=guide.language,
        fallback_language=fallback_language,
        sections=guide.sections,
        official_source_url=guide.official_source_url,
    )


# ---------------------------------------------------------------------------
# /help/search
# ---------------------------------------------------------------------------


@router.get("/search", response_model=HelpSearchResponse)
async def help_search(
    q: str = Query("", min_length=0, max_length=200),
    language: HelpLanguage = Query("cs"),
    _user_id: UUID = Depends(get_current_user_id),
) -> HelpSearchResponse:
    content = get_help_content()
    results = run_search(content, q, language)
    return HelpSearchResponse(query=q, results_count=len(results), results=results)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_ui(content: HelpContent, language: HelpLanguage):
    """Fetch UI strings for the requested language, falling back to cs."""
    locales = content.ui_strings.translations
    return locales.get(language) or locales["cs"]


def _popular_categories(content: HelpContent, language: HelpLanguage) -> list[HelpPopularCategory]:
    """Order = explicit popular order, then file order for any extras."""
    ordered_slugs = list(_POPULAR_CATEGORY_ORDER)
    seen = set(ordered_slugs)
    for category in content.categories:
        if category.slug not in seen:
            ordered_slugs.append(category.slug)
            seen.add(category.slug)

    out: list[HelpPopularCategory] = []
    for slug in ordered_slugs:
        category = content.find_category(slug)
        if category is None:
            continue
        translation = category.translations.get(language) or category.translations["cs"]
        out.append(
            HelpPopularCategory(
                slug=category.slug,
                title=translation.title,
                subtitle=translation.subtitle,
                badge=translation.badge,
                icon=category.icon,
            )
        )
    return out


def _language_fallback_order(language: HelpLanguage) -> tuple[HelpLanguage, ...]:
    ordered: list[HelpLanguage] = [language]
    for fallback in _GUIDE_FALLBACK_ORDER:
        if fallback not in ordered:
            ordered.append(fallback)
    return tuple(ordered)


def _published_guide_with_fallback(
    langs: dict[HelpLanguage, HelpGuide], language: HelpLanguage
) -> HelpGuide | None:
    for candidate in _language_fallback_order(language):
        guide = langs.get(candidate)
        if guide and guide.published:
            return guide
    return None


def _published_guides_with_fallback(
    content: HelpContent, category_slug: str, language: HelpLanguage
) -> list[HelpGuide]:
    out: list[HelpGuide] = []
    for langs in content.guides_by_slug.values():
        guide = _published_guide_with_fallback(langs, language)
        if guide and guide.category == category_slug:
            out.append(guide)
    return out


def _ordered_guide_cards(order: list[str], guides) -> list[HelpGuideCard]:
    """Apply category.guides_order, then append any not listed in author order."""
    by_slug = {guide.slug: guide for guide in guides}
    out: list[HelpGuideCard] = []
    seen: set[str] = set()
    for slug in order:
        guide = by_slug.get(slug)
        if guide:
            out.append(HelpGuideCard(slug=guide.slug, title=guide.title, subtitle=guide.subtitle))
            seen.add(slug)
    for guide in guides:
        if guide.slug not in seen:
            out.append(HelpGuideCard(slug=guide.slug, title=guide.title, subtitle=guide.subtitle))
    return out


def _resolve_useful_actions(content: HelpContent, override, language: HelpLanguage) -> list[HelpUsefulAction]:
    """Per-category override wins; otherwise the default block in code."""
    ui = _resolve_ui(content, language)
    raw_actions: list[dict[str, object]] = (
        [{"key": a.key, "action": a.action, "highlighted": a.highlighted} for a in override]
        if override
        else _DEFAULT_USEFUL_ACTIONS
    )

    out: list[HelpUsefulAction] = []
    for entry in raw_actions:
        key = str(entry["key"])
        strings = ui.useful_actions.get(key)
        if strings is None:
            logger.warning("Useful action '%s' has no string for language=%s — skipped", key, language)
            continue
        out.append(
            HelpUsefulAction(
                key=key,
                title=strings.title,
                subtitle=strings.subtitle,
                action=str(entry["action"]),
                highlighted=bool(entry.get("highlighted", False)),
            )
        )
    return out
