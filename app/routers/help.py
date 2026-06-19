"""GET /help/* — read endpoints for the Help tab.

As of migration 0011_help_admin, guides live in the `help_guides` table
and are written through the standalone web admin (help-admin/index.html).
Category UI metadata (hero block, fallback block, useful_actions override,
guides_order) and the help_ui_strings.yaml lookup map are still loaded
from help_content/*.yaml at startup — the admin doesn't edit those.

Per-request we fetch published guides from Postgres, then assemble a
HelpContent-shaped snapshot so the existing response composer + search
ranker keep working unchanged. The Supabase backend client uses the
service key and bypasses RLS, so we filter status='published' explicitly.
"""

from __future__ import annotations

import asyncio
import logging
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError

from app.middleware.auth import get_active_user_id
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
    HelpSection,
    HelpSectionChecklist,
    HelpSectionSteps,
    HelpSectionText,
    HelpUiLocale,
    HelpUsefulAction,
    HelpUsefulActionDef,
)
from app.routers.profile import _ensure_profile
from app.services.help_loader import HelpContent, get_help_content
from app.services.help_search import search as run_search
from app.services.rag import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/help", tags=["help"])

_QUICK_ACTION_ROUTES: dict[str, str] = {
    "check_status": "/help/guides/check-status",
    "understand_letter": "/scan/new",
}

_DEFAULT_USEFUL_ACTIONS: list[dict[str, object]] = [
    {"key": "add_deadline_manual", "action": "deadline_new", "highlighted": False},
    {"key": "open_deadlines", "action": "deadlines_tab", "highlighted": False},
]

_POPULAR_CATEGORY_ORDER: tuple[str, ...] = ("visa-pobyt", "taxes", "bills", "urad-prace")


# ---------------------------------------------------------------------------
# DB → HelpGuide hydration
# ---------------------------------------------------------------------------


async def _load_published_guides() -> dict[str, dict[HelpLanguage, HelpGuide]]:
    """Fetch all published guides from Postgres and return the same
    guides_by_slug shape the YAML loader used to produce."""
    supabase = await get_supabase()
    res = await (
        supabase.table("help_guides")
        .select(
            "slug, category_slug, language, status, title, subtitle, "
            "short_summary, keywords, official_source_url, updated_at, sections"
        )
        .eq("status", "published")
        .execute()
    )
    rows = res.data or []

    out: dict[str, dict[HelpLanguage, HelpGuide]] = {}
    for row in rows:
        try:
            guide = HelpGuide.model_validate(
                {
                    "slug": row["slug"],
                    "category": row["category_slug"],
                    "language": row["language"],
                    "published": row["status"] == "published",
                    "title": row["title"],
                    "subtitle": row.get("subtitle"),
                    "short_summary": row.get("short_summary"),
                    "keywords": row.get("keywords") or [],
                    "official_source_url": row.get("official_source_url"),
                    "updated_at": row.get("updated_at"),
                    "sections": row.get("sections") or [],
                }
            )
        except ValidationError:
            logger.exception(
                "Skipping malformed help_guides row slug=%s lang=%s",
                row.get("slug"),
                row.get("language"),
            )
            continue
        out.setdefault(guide.slug, {})[guide.language] = guide
    return out


# Snapshot TTL cache: guides change rarely (admin edits), but every Help-tab
# request was fetching the whole help_guides table. 60s staleness is invisible
# to users and removes the per-request full-table read.
_SNAPSHOT_TTL_SECONDS = 60.0
_snapshot_cache: tuple[float, HelpContent] | None = None
_snapshot_lock = asyncio.Lock()


async def _snapshot() -> HelpContent:
    """HelpContent assembled from YAML (categories + ui_strings) + DB (guides).

    Categories metadata still comes from YAML — the new help_categories
    table only holds basics the admin edits, and the mobile response model
    needs the rich fields. Cached for _SNAPSHOT_TTL_SECONDS.
    """
    global _snapshot_cache
    now = time.monotonic()
    if _snapshot_cache is not None and now - _snapshot_cache[0] < _SNAPSHOT_TTL_SECONDS:
        return _snapshot_cache[1]

    async with _snapshot_lock:
        # Re-check under the lock — a concurrent request may have refreshed it.
        now = time.monotonic()
        if _snapshot_cache is not None and now - _snapshot_cache[0] < _SNAPSHOT_TTL_SECONDS:
            return _snapshot_cache[1]

        yaml_content = get_help_content()
        guides_by_slug = await _load_published_guides()
        content = HelpContent(
            categories=yaml_content.categories,
            guides_by_slug=guides_by_slug,
            ui_strings=yaml_content.ui_strings,
        )
        _snapshot_cache = (time.monotonic(), content)
        return content


def invalidate_snapshot_cache() -> None:
    """Drop the cached snapshot (admin writes + tests)."""
    global _snapshot_cache
    _snapshot_cache = None


# ---------------------------------------------------------------------------
# /help/home
# ---------------------------------------------------------------------------


@router.get("/home", response_model=HelpHomeResponse)
async def help_home(
    language: HelpLanguage = Query("cs"),
    _user_id: UUID = Depends(get_active_user_id),
) -> HelpHomeResponse:
    content = await _snapshot()
    ui = _resolve_ui(content, language)

    quick_actions: list[HelpQuickAction] = []
    for key, route in _QUICK_ACTION_ROUTES.items():
        strings = ui.quick_actions.get(key)
        if strings is None:
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
    _user_id: UUID = Depends(get_active_user_id),
) -> HelpCategoryResponse:
    content = await _snapshot()
    category = content.find_category(slug)
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    translation = category.translations.get(language) or category.translations["cs"]
    fallback_translation = category.fallback.get(language) or category.fallback["cs"]

    guides_in_language = _published_guides_for_language(content, slug, language)
    is_completely_empty = not guides_in_language

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
    user_id: UUID = Depends(get_active_user_id),
) -> HelpGuideResponse:
    content = await _snapshot()
    langs = content.guides_by_slug.get(slug)
    if not langs:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guide not found")

    guide = _published_guide_for_language(langs, language)
    if guide is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Guide not available")

    category = content.find_category(guide.category)
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Guide references unknown category",
        )

    cat_translation = category.translations.get(language) or category.translations["cs"]

    # Pro gate is enforced here, server-side: a free user receives only a short
    # real teaser (first line/chars) of each pro_only section — the bulk of the
    # Pro content stays server-side. The client renders that teaser blurred
    # behind the upgrade gate. plan is updated solely through the RevenueCat
    # webhook.
    profile = await _ensure_profile(user_id)
    plan = profile.get("plan", "free")
    sections = _gate_sections_for_plan(guide.sections, plan)

    return HelpGuideResponse(
        slug=guide.slug,
        category=HelpGuideCategoryRef(slug=category.slug, title=cat_translation.title),
        title=guide.title,
        subtitle=guide.subtitle,
        short_summary=guide.short_summary,
        language=guide.language,
        fallback_language=None,
        sections=sections,
        official_source_url=guide.official_source_url,
        updated_at=guide.updated_at,
    )


# Section types whose pro_only content is gated. warning/cta are always shown
# in full — they carry disclaimers and navigation, not the Pro payload. This
# mirrors the client's gating condition in features/help/components.tsx.
_GATED_SECTION_TYPES = ("text", "steps", "checklist")


# How much of a pro_only section to expose as a free-user teaser. Generous
# enough to look like real content under the blur, small enough that the bulk
# of the Pro payload stays server-side.
_PREVIEW_CHARS = 140


def _gate_sections_for_plan(sections: list[HelpSection], plan: str) -> list[HelpSection]:
    """Return sections with pro_only content reduced to a teaser for free users.

    Pro users get the untouched list. For free users, every pro_only
    text/steps/checklist section is replaced by a copy that keeps its type +
    title + a short real teaser (the first chars / first item) and is flagged
    `locked=True`. The client renders that teaser blurred behind the upgrade
    gate. The bulk of the Pro content never crosses the API boundary. The
    cached snapshot's section objects are never mutated — previews build fresh
    instances.
    """
    if plan == "pro":
        return sections

    gated: list[HelpSection] = []
    for section in sections:
        if section.pro_only and section.type in _GATED_SECTION_TYPES:
            gated.append(_preview_section(section))
        else:
            gated.append(section)
    return gated


def _preview_text(body: str) -> str:
    """First ~_PREVIEW_CHARS of body, cut on a word boundary with an ellipsis."""
    body = body.strip()
    if len(body) <= _PREVIEW_CHARS:
        return body
    head = body[:_PREVIEW_CHARS].rsplit(" ", 1)[0].rstrip()
    return f"{head}…"


def _preview_section(section: HelpSection) -> HelpSection:
    if section.type == "text":
        return HelpSectionText(
            type="text", title=section.title, body=_preview_text(section.body), pro_only=True, locked=True
        )
    if section.type == "steps":
        preview = [_preview_text(section.items[0])] if section.items else []
        return HelpSectionSteps(type="steps", title=section.title, items=preview, pro_only=True, locked=True)
    if section.type == "checklist":
        preview = [_preview_text(section.items[0])] if section.items else []
        return HelpSectionChecklist(
            type="checklist", title=section.title, items=preview, pro_only=True, locked=True
        )
    # Defensive: only the gated types reach here.
    return section


# ---------------------------------------------------------------------------
# /help/search
# ---------------------------------------------------------------------------


@router.get("/search", response_model=HelpSearchResponse)
async def help_search(
    q: str = Query("", min_length=0, max_length=200),
    language: HelpLanguage = Query("cs"),
    _user_id: UUID = Depends(get_active_user_id),
) -> HelpSearchResponse:
    content = await _snapshot()
    results = run_search(content, q, language)
    return HelpSearchResponse(query=q, results_count=len(results), results=results)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_ui(content: HelpContent, language: HelpLanguage) -> HelpUiLocale:
    locales = content.ui_strings.translations
    return locales.get(language) or locales["cs"]


def _popular_categories(content: HelpContent, language: HelpLanguage) -> list[HelpPopularCategory]:
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


def _published_guide_for_language(
    langs: dict[HelpLanguage, HelpGuide], language: HelpLanguage
) -> HelpGuide | None:
    guide = langs.get(language)
    if guide and guide.published:
        return guide
    return None


def _published_guides_for_language(
    content: HelpContent, category_slug: str, language: HelpLanguage
) -> list[HelpGuide]:
    out: list[HelpGuide] = []
    for langs in content.guides_by_slug.values():
        guide = _published_guide_for_language(langs, language)
        if guide and guide.category == category_slug:
            out.append(guide)
    return out


def _ordered_guide_cards(order: list[str], guides: list[HelpGuide]) -> list[HelpGuideCard]:
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


def _resolve_useful_actions(
    content: HelpContent,
    override: list[HelpUsefulActionDef] | None,
    language: HelpLanguage,
) -> list[HelpUsefulAction]:
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
