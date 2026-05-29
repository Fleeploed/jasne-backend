"""Pydantic models for the Help section.

Two layers:
1. Source models — match the YAML on disk one-to-one. Used by `help_loader`
   to parse and validate `help_content/*.yaml` at app startup.
2. Response models — what /help/* endpoints return. Composed from source
   models + UI-shell strings (quick_actions / scan_cta / useful_actions).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HelpLanguage = Literal["cs", "en", "ru", "uk"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Source models (mirror the YAML files)
# ---------------------------------------------------------------------------


class HelpSectionText(_Base):
    type: Literal["text"]
    title: str | None = None
    body: str


class HelpSectionSteps(_Base):
    type: Literal["steps"]
    title: str | None = None
    items: list[str] = Field(min_length=1)


class HelpSectionChecklist(_Base):
    type: Literal["checklist"]
    title: str | None = None
    items: list[str] = Field(min_length=1)


class HelpSectionWarning(_Base):
    type: Literal["warning"]
    title: str | None = None
    body: str


class HelpSectionCta(_Base):
    type: Literal["cta"]
    title: str | None = None
    actions: list[str] = Field(min_length=1)


HelpSection = HelpSectionText | HelpSectionSteps | HelpSectionChecklist | HelpSectionWarning | HelpSectionCta


class HelpGuide(_Base):
    """One YAML file per guide per language."""

    slug: str
    category: str
    language: HelpLanguage
    published: bool = True
    title: str
    subtitle: str | None = None
    short_summary: str | None = None
    keywords: list[str] = Field(default_factory=list)
    official_source_url: str | None = None
    updated_at: datetime | None = None
    sections: list[HelpSection] = Field(min_length=1)


class HelpCategoryTranslation(_Base):
    title: str
    subtitle: str | None = None
    badge: str | None = None
    hero_title: str | None = None
    hero_text: str | None = None


class HelpCategoryFallback(_Base):
    title: str
    text: str


class HelpUsefulActionDef(_Base):
    """Per-category override of the default useful_actions block."""

    key: str
    action: str
    highlighted: bool = False


class HelpCategory(_Base):
    slug: str
    icon: str | None = None
    translations: dict[HelpLanguage, HelpCategoryTranslation]
    fallback: dict[HelpLanguage, HelpCategoryFallback]
    # Explicit guide ordering — filesystem order is not stable across OSes.
    guides_order: list[str] = Field(default_factory=list)
    # Optional override: keys reference entries in help_ui_strings.useful_actions
    useful_actions: list[HelpUsefulActionDef] | None = None


# ---------------------------------------------------------------------------
# UI-shell strings (help_ui_strings.yaml — small lookup map)
# ---------------------------------------------------------------------------


class QuickActionStrings(_Base):
    title: str
    subtitle: str


class ScanCtaStrings(_Base):
    title: str
    subtitle: str
    primary_button: str
    helper_text: str | None = None
    pro_hint: str | None = None


class UsefulActionStrings(_Base):
    title: str
    subtitle: str | None = None


class HelpUiLocale(_Base):
    """All localized UI strings for a single language."""

    quick_actions: dict[str, QuickActionStrings]
    scan_cta: ScanCtaStrings
    useful_actions: dict[str, UsefulActionStrings]


class HelpUiStrings(_Base):
    """Top level of help_ui_strings.yaml — keyed by language."""

    translations: dict[HelpLanguage, HelpUiLocale]


# ---------------------------------------------------------------------------
# Response models (returned by /help/* endpoints)
# ---------------------------------------------------------------------------


class HelpQuickAction(_Base):
    key: str
    title: str
    subtitle: str
    route: str


class HelpPopularCategory(_Base):
    slug: str
    title: str
    subtitle: str | None = None
    badge: str | None = None
    icon: str | None = None


class HelpScanCta(_Base):
    title: str
    subtitle: str
    primary_button: str
    primary_action: str
    helper_text: str | None = None
    pro_hint: str | None = None


class HelpHomeResponse(_Base):
    quick_actions: list[HelpQuickAction]
    popular_categories: list[HelpPopularCategory]
    scan_cta: HelpScanCta


class HelpGuideCard(_Base):
    slug: str
    title: str
    subtitle: str | None = None


class HelpCategoryHero(_Base):
    title: str | None = None
    text: str | None = None
    badge: str | None = None


class HelpUsefulAction(_Base):
    key: str
    title: str
    subtitle: str | None = None
    action: str
    highlighted: bool = False


class HelpFallbackBlock(_Base):
    title: str
    text: str


class HelpCategoryResponse(_Base):
    slug: str
    title: str
    subtitle: str | None = None
    hero: HelpCategoryHero
    guides: list[HelpGuideCard]
    fallback: HelpFallbackBlock | None = None
    useful_actions: list[HelpUsefulAction]


class HelpGuideCategoryRef(_Base):
    slug: str
    title: str


class HelpGuideResponse(_Base):
    slug: str
    category: HelpGuideCategoryRef
    title: str
    subtitle: str | None = None
    short_summary: str | None = None
    language: HelpLanguage
    fallback_language: HelpLanguage | None = None
    sections: list[HelpSection]
    official_source_url: str | None = None
    updated_at: datetime | None = None


class HelpSearchResult(_Base):
    type: Literal["guide", "category"]
    slug: str
    title: str
    subtitle: str | None = None
    category_title: str | None = None


class HelpSearchResponse(_Base):
    query: str
    results_count: int
    results: list[HelpSearchResult]
