"""Deadline validation — last line of defense against hallucinated deadlines.

Skill: „Одна галлюцинация убивает доверие". The analyzer LLM is instructed
to only use OCR-derived dates, but we still strip out anything that:
  - is in the past (we won't push-notify about expired deadlines)
  - is unreasonably far in the future (>3 years suggests hallucination)
  - has an unknown/invalid type
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from app.models.schemas import DeadlineItem, DeadlineType

logger = logging.getLogger(__name__)

# Skill: "<3 лет" — anything beyond looks made-up.
MAX_DEADLINE_HORIZON: timedelta = timedelta(days=3 * 365)

VALID_TYPES: frozenset[str] = frozenset({"payment", "action", "response", "expiration"})
SMLUVY_CRITICAL_KEYWORDS: frozenset[str] = frozenset(
    {
        "konec",
        "konci",
        "končí",
        "ukonceni",
        "ukončení",
        "vypoved",
        "výpověď",
        "vypovedni",
        "výpovědní",
        "termination",
        "expires",
        "expiration",
        "price increase",
        "zvyseni",
        "zvýšení",
        "zdrazeni",
        "zdražení",
    }
)


def _strip_accents(text: str) -> str:
    table = str.maketrans(
        {
            "á": "a",
            "č": "c",
            "ď": "d",
            "é": "e",
            "ě": "e",
            "í": "i",
            "ň": "n",
            "ó": "o",
            "ř": "r",
            "š": "s",
            "ť": "t",
            "ú": "u",
            "ů": "u",
            "ý": "y",
            "ž": "z",
        }
    )
    return text.lower().translate(table)


def _date_variants(value: date) -> set[str]:
    return {
        value.isoformat(),
        f"{value.day}.{value.month}.{value.year}",
        f"{value.day}. {value.month}. {value.year}",
        f"{value.day:02d}.{value.month:02d}.{value.year}",
        f"{value.day:02d}. {value.month:02d}. {value.year}",
        f"{value.day:02d}.{value.month:02d}.{str(value.year)[2:]}",
        f"{value.day}. {value.month}. {str(value.year)[2:]}",
    }


def _date_appears_in_source(value: date, source_text: str) -> bool:
    compact_source = re.sub(r"\s+", " ", source_text)
    return any(variant in compact_source for variant in _date_variants(value))


def filter_valid_deadlines(
    deadlines: list[DeadlineItem],
    today: date | None = None,
    source_text: str | None = None,
    category: str | None = None,
) -> tuple[list[DeadlineItem], list[str]]:
    """Return (kept, rejected_reasons). Caller logs rejected; analyzer schema
    forbids them downstream."""
    today = today or date.today()
    horizon = today + MAX_DEADLINE_HORIZON
    kept: list[DeadlineItem] = []
    rejected: list[str] = []

    for d in deadlines:
        if d.date < today:
            rejected.append(f"in the past: {d.date.isoformat()} '{d.description[:40]}'")
            continue
        if d.date > horizon:
            rejected.append(f"too far ({d.date.isoformat()}, >3y) '{d.description[:40]}'")
            continue
        if d.type not in VALID_TYPES:
            rejected.append(f"invalid type='{d.type}' '{d.description[:40]}'")
            continue
        if not d.description.strip():
            rejected.append(f"empty description for {d.date.isoformat()}")
            continue
        if source_text is not None and not _date_appears_in_source(d.date, source_text):
            rejected.append(f"date not found in OCR: {d.date.isoformat()} '{d.description[:40]}'")
            continue
        if category == "ostatni":
            rejected.append(f"category ostatni has no automatic deadlines: {d.date.isoformat()}")
            continue
        if category == "smlouvy":
            normalized_description = _strip_accents(d.description)
            if not any(keyword in normalized_description for keyword in SMLUVY_CRITICAL_KEYWORDS):
                rejected.append(f"non-critical smlouvy deadline: {d.date.isoformat()} '{d.description[:40]}'")
                continue
        kept.append(d)

    return kept, rejected


def coerce_deadline_type(value: str) -> DeadlineType:
    """Map free-form LLM output to one of our 4 valid deadline types."""
    if value in VALID_TYPES:
        return value  # type: ignore[return-value]
    return "action"  # safest fallback — neutral non-payment, non-expiration
