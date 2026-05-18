"""YAML loader for the Help section.

Loads `help_content/categories.yaml`, `help_content/help_ui_strings.yaml`,
and all guides under `help_content/guides/<category>/<slug>.<lang>.yaml`
into a frozen in-memory structure at app startup.

Validation rules (any violation → HelpContentError, aborting startup):
* Every category must declare a `cs` translation and a `cs` fallback.
* Every guide's `category` field must reference an existing category.
* Every guide's `language` claim must match the filename suffix.
* The same `slug` cannot be reused across categories (slugs are global).
* The same `(slug, language)` pair cannot appear twice.
* Section types validated by Pydantic discriminator.

The loaded `HelpContent` is exposed as a module-level singleton via
`get_help_content()`. `load_help_content(path)` is called once from the
FastAPI lifespan; tests can construct their own `HelpContent` and inject
it via `set_help_content_for_testing()`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.models.help_schemas import (
    HelpCategory,
    HelpGuide,
    HelpLanguage,
    HelpUiStrings,
)

logger = logging.getLogger(__name__)

# Repo root → backend root → help_content. Resolved at module load time so
# the path is stable across CWD changes (Railway runs from /app).
DEFAULT_CONTENT_ROOT = Path(__file__).resolve().parents[2] / "help_content"


class HelpContentError(RuntimeError):
    """Raised when the on-disk content fails validation."""


@dataclass(frozen=True)
class HelpContent:
    """Frozen in-memory snapshot of all Help YAML files.

    Reads are O(1) by slug; iteration over categories is order-preserving
    (the order from categories.yaml).
    """

    categories: tuple[HelpCategory, ...]
    # slug → language → guide. Empty inner dict means the slug is declared
    # nowhere (impossible after validation; kept for type clarity).
    guides_by_slug: dict[str, dict[HelpLanguage, HelpGuide]]
    ui_strings: HelpUiStrings

    # ---- convenience accessors ----

    def find_category(self, slug: str) -> HelpCategory | None:
        return next((c for c in self.categories if c.slug == slug), None)

    def guides_for_category(self, category_slug: str) -> list[HelpGuide]:
        """All guides whose category matches. Language-agnostic; caller
        filters by language before serving."""
        out: list[HelpGuide] = []
        for langs in self.guides_by_slug.values():
            for guide in langs.values():
                if guide.category == category_slug:
                    out.append(guide)
                    break  # one slot per slug — same guide in other langs
        return out

    def published_guides_in_language(self, category_slug: str, language: HelpLanguage) -> list[HelpGuide]:
        """Published guides in the requested language only. No fallback to cs."""
        out: list[HelpGuide] = []
        for _slug, langs in self.guides_by_slug.items():
            guide = langs.get(language)
            if guide and guide.category == category_slug and guide.published:
                out.append(guide)
        return out


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_help_content: HelpContent | None = None


def get_help_content() -> HelpContent:
    if _help_content is None:
        raise RuntimeError(
            "Help content has not been loaded. Did the FastAPI lifespan run load_help_content()?"
        )
    return _help_content


def set_help_content_for_testing(content: HelpContent) -> None:
    """Tests bypass YAML loading and inject a hand-built HelpContent."""
    global _help_content
    _help_content = content


def reset_help_content_for_testing() -> None:
    global _help_content
    _help_content = None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_help_content(root: Path | None = None) -> HelpContent:
    """Parse + validate all YAML files. Cache the result in the singleton.

    Raises HelpContentError on any validation failure — startup must abort
    rather than serve partial content."""
    global _help_content
    root_path = root or DEFAULT_CONTENT_ROOT
    if not root_path.exists():
        raise HelpContentError(f"Help content root does not exist: {root_path}")

    categories = _load_categories(root_path / "categories.yaml")
    category_slugs = {c.slug for c in categories}

    ui_strings = _load_ui_strings(root_path / "help_ui_strings.yaml")

    guides_by_slug: dict[str, dict[HelpLanguage, HelpGuide]] = {}
    slug_to_category: dict[str, str] = {}

    guides_root = root_path / "guides"
    if guides_root.exists():
        for guide_file in sorted(guides_root.rglob("*.yaml")):
            guide = _load_guide(guide_file, category_slugs)

            # Globally unique slug — same slug in different categories is
            # a content authoring error.
            existing_category = slug_to_category.get(guide.slug)
            if existing_category and existing_category != guide.category:
                raise HelpContentError(
                    f"Slug '{guide.slug}' is used in both category "
                    f"'{existing_category}' and '{guide.category}' "
                    f"({guide_file}). Slugs must be globally unique."
                )
            slug_to_category[guide.slug] = guide.category

            langs = guides_by_slug.setdefault(guide.slug, {})
            if guide.language in langs:
                raise HelpContentError(
                    f"Duplicate guide '{guide.slug}' in language '{guide.language}' ({guide_file})."
                )
            langs[guide.language] = guide

    content = HelpContent(
        categories=tuple(categories),
        guides_by_slug=guides_by_slug,
        ui_strings=ui_strings,
    )
    _help_content = content

    logger.info(
        "Help content loaded: %d categories, %d guides (across all languages)",
        len(categories),
        sum(len(langs) for langs in guides_by_slug.values()),
    )
    return content


def _read_yaml(path: Path) -> Any:
    if not path.exists():
        raise HelpContentError(f"Required help file missing: {path}")
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise HelpContentError(f"YAML parse error in {path}: {exc}") from exc


def _load_categories(path: Path) -> list[HelpCategory]:
    raw = _read_yaml(path)
    if not isinstance(raw, list):
        raise HelpContentError(f"categories.yaml must be a top-level list, got {type(raw).__name__}")

    out: list[HelpCategory] = []
    seen_slugs: set[str] = set()
    for entry in raw:
        try:
            category = HelpCategory.model_validate(entry)
        except ValidationError as exc:
            raise HelpContentError(f"Invalid category in {path}: {exc}") from exc

        if category.slug in seen_slugs:
            raise HelpContentError(f"Duplicate category slug: {category.slug}")
        seen_slugs.add(category.slug)

        # cs is the canonical fallback — every category needs it.
        if "cs" not in category.translations:
            raise HelpContentError(f"Category '{category.slug}' is missing the required 'cs' translation.")
        if "cs" not in category.fallback:
            raise HelpContentError(f"Category '{category.slug}' is missing the required 'cs' fallback.")
        out.append(category)
    return out


def _load_ui_strings(path: Path) -> HelpUiStrings:
    raw = _read_yaml(path)
    try:
        ui = HelpUiStrings.model_validate(raw)
    except ValidationError as exc:
        raise HelpContentError(f"Invalid help_ui_strings.yaml: {exc}") from exc

    if "cs" not in ui.translations:
        raise HelpContentError("help_ui_strings.yaml is missing the required 'cs' translations.")
    return ui


def _load_guide(path: Path, category_slugs: set[str]) -> HelpGuide:
    raw = _read_yaml(path)
    try:
        guide = HelpGuide.model_validate(raw)
    except ValidationError as exc:
        raise HelpContentError(f"Invalid guide in {path}: {exc}") from exc

    # Filename convention: <slug>.<lang>.yaml — verify language matches.
    name_parts = path.stem.split(".")
    if len(name_parts) != 2:
        raise HelpContentError(f"Guide filename must be '<slug>.<lang>.yaml', got '{path.name}'")
    file_slug, file_lang = name_parts
    if file_slug != guide.slug:
        raise HelpContentError(f"Guide slug mismatch: file '{path.name}' has slug='{guide.slug}'")
    if file_lang != guide.language:
        raise HelpContentError(f"Guide language mismatch: file '{path.name}' has language='{guide.language}'")

    if guide.category not in category_slugs:
        raise HelpContentError(
            f"Guide '{guide.slug}' references unknown category '{guide.category}' "
            f"({path}). Add it to categories.yaml first."
        )
    return guide
