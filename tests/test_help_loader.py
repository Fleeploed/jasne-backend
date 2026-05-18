"""Tests for app/services/help_loader.py.

Each test builds a tiny content tree in a tmp_path so it doesn't depend
on the real `backend/help_content/` directory.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from app.services.help_loader import HelpContentError, load_help_content


# ---------------------------------------------------------------------------
# Helpers — build a valid minimal content tree, then mutate
# ---------------------------------------------------------------------------


VALID_CATEGORIES = dedent(
    """\
    - slug: visa-pobyt
      icon: passport
      translations:
        cs:
          title: "Vízum a pobyt"
          subtitle: "OAMP"
      fallback:
        cs:
          title: "Zatím prázdné"
          text: "Návody připravujeme."
    """
)

VALID_UI_STRINGS = dedent(
    """\
    translations:
      cs:
        quick_actions:
          check_status:
            title: "Zkontrolovat stav"
            subtitle: "OAMP"
        scan_cta:
          title: "Máte dokument?"
          subtitle: "Vyfoťte ho"
          primary_button: "Skenovat"
        useful_actions:
          scan_oamp_letter:
            title: "Naskenovat OAMP"
    """
)

VALID_GUIDE = dedent(
    """\
    slug: check-status
    category: visa-pobyt
    language: cs
    title: "Jak zkontrolovat stav"
    sections:
      - type: text
        body: "Otevřete frs.gov.cz a zadejte číslo žádosti."
      - type: cta
        actions: [scan_document]
    """
)


def _build_tree(root: Path, *, categories: str = VALID_CATEGORIES, ui: str = VALID_UI_STRINGS, guides: dict[str, str] | None = None) -> Path:
    """Write a content tree under `root` and return root."""
    (root / "categories.yaml").write_text(categories, encoding="utf-8")
    (root / "help_ui_strings.yaml").write_text(ui, encoding="utf-8")

    guides_dir = root / "guides"
    guides_dir.mkdir(parents=True, exist_ok=True)
    for relative_path, body in (guides or {}).items():
        full = guides_dir / relative_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(body, encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_loads_valid_content(tmp_path):
    root = _build_tree(
        tmp_path,
        guides={"visa-pobyt/check-status.cs.yaml": VALID_GUIDE},
    )
    content = load_help_content(root)

    assert len(content.categories) == 1
    assert content.categories[0].slug == "visa-pobyt"
    assert "check-status" in content.guides_by_slug
    assert content.guides_by_slug["check-status"]["cs"].title == "Jak zkontrolovat stav"
    assert "cs" in content.ui_strings.translations


def test_loads_empty_guides_dir(tmp_path):
    root = _build_tree(tmp_path)
    content = load_help_content(root)
    assert content.categories[0].slug == "visa-pobyt"
    assert content.guides_by_slug == {}


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


def test_rejects_invalid_section_type(tmp_path):
    bad_guide = VALID_GUIDE.replace("type: text", "type: definitelynotreal")
    root = _build_tree(
        tmp_path,
        guides={"visa-pobyt/check-status.cs.yaml": bad_guide},
    )
    with pytest.raises(HelpContentError):
        load_help_content(root)


def test_rejects_guide_referencing_unknown_category(tmp_path):
    orphan = VALID_GUIDE.replace("category: visa-pobyt", "category: does-not-exist")
    root = _build_tree(
        tmp_path,
        guides={"visa-pobyt/check-status.cs.yaml": orphan},
    )
    with pytest.raises(HelpContentError, match="unknown category"):
        load_help_content(root)


def test_rejects_duplicate_slug_in_same_language(tmp_path):
    # Same slug in two files for the same lang — duplicate registration.
    other_lang = dedent(
        """\
        slug: check-status
        category: visa-pobyt
        language: cs
        title: "Duplicate"
        sections:
          - type: text
            body: "x"
        """
    )
    root = _build_tree(
        tmp_path,
        guides={
            "visa-pobyt/check-status.cs.yaml": VALID_GUIDE,
            "visa-pobyt/other-name.cs.yaml": other_lang,
        },
    )
    with pytest.raises(HelpContentError):
        load_help_content(root)


def test_rejects_slug_reuse_across_categories(tmp_path):
    """Even in different categories — slug is global."""
    second_category_block = VALID_CATEGORIES + dedent(
        """\

        - slug: taxes
          icon: receipt
          translations:
            cs:
              title: "Daně"
          fallback:
            cs:
              title: "Prázdné"
              text: "Připravujeme"
        """
    )
    second_guide = VALID_GUIDE.replace("category: visa-pobyt", "category: taxes")
    root = _build_tree(
        tmp_path,
        categories=second_category_block,
        guides={
            "visa-pobyt/check-status.cs.yaml": VALID_GUIDE,
            "taxes/check-status.cs.yaml": second_guide,
        },
    )
    with pytest.raises(HelpContentError, match="globally unique"):
        load_help_content(root)


def test_rejects_category_without_cs_translation(tmp_path):
    only_ru = dedent(
        """\
        - slug: visa-pobyt
          translations:
            ru:
              title: "Только русский"
          fallback:
            cs:
              title: "x"
              text: "x"
        """
    )
    root = _build_tree(tmp_path, categories=only_ru)
    with pytest.raises(HelpContentError, match="cs"):
        load_help_content(root)


def test_rejects_category_without_cs_fallback(tmp_path):
    no_cs_fallback = dedent(
        """\
        - slug: visa-pobyt
          translations:
            cs:
              title: "OK"
          fallback:
            ru:
              title: "x"
              text: "x"
        """
    )
    root = _build_tree(tmp_path, categories=no_cs_fallback)
    with pytest.raises(HelpContentError, match="cs"):
        load_help_content(root)


def test_rejects_empty_steps_items(tmp_path):
    empty_steps = dedent(
        """\
        slug: check-status
        category: visa-pobyt
        language: cs
        title: "Bad"
        sections:
          - type: steps
            items: []
        """
    )
    root = _build_tree(
        tmp_path,
        guides={"visa-pobyt/check-status.cs.yaml": empty_steps},
    )
    with pytest.raises(HelpContentError):
        load_help_content(root)


def test_rejects_filename_slug_mismatch(tmp_path):
    root = _build_tree(
        tmp_path,
        guides={"visa-pobyt/wrong-name.cs.yaml": VALID_GUIDE},
    )
    with pytest.raises(HelpContentError, match="slug"):
        load_help_content(root)


def test_rejects_filename_language_mismatch(tmp_path):
    root = _build_tree(
        tmp_path,
        guides={"visa-pobyt/check-status.ru.yaml": VALID_GUIDE},
    )
    with pytest.raises(HelpContentError, match="language"):
        load_help_content(root)


def test_aborts_on_missing_root(tmp_path):
    missing = tmp_path / "no-such-dir"
    with pytest.raises(HelpContentError, match="does not exist"):
        load_help_content(missing)


def test_aborts_on_malformed_yaml(tmp_path):
    (tmp_path / "categories.yaml").write_text("- slug: visa\n  translations: {{{", encoding="utf-8")
    (tmp_path / "help_ui_strings.yaml").write_text(VALID_UI_STRINGS, encoding="utf-8")
    with pytest.raises(HelpContentError):
        load_help_content(tmp_path)
