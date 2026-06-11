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
# Matched against an accent-stripped description, so keys are accent-free.
SMLUVY_CRITICAL_KEYWORDS: frozenset[str] = frozenset(
    {
        "konec",
        "konci",
        "ukonceni",
        "vypoved",
        "vypovedni",
        "termination",
        "expires",
        "expiration",
        "expirace",
        "price increase",
        "zvyseni",
        "zdrazeni",
        # Real contract-end phrasings that previously slipped through as
        # "non-critical": vyprší, platnost do, fixace, doba určitá.
        "vyprsi",
        "vyprseni",
        "platnost",
        "fixace",
        "fixovan",
        "doba urcita",
        "doba urciteho",
        "prodlouzeni",
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


# Czech month names in genitive, as they appear in dates ("15. května 2026").
# Accent-stripped to match the normalized source.
_CZECH_MONTHS_GENITIVE: dict[int, str] = {
    1: "ledna",
    2: "unora",
    3: "brezna",
    4: "dubna",
    5: "kvetna",
    6: "cervna",
    7: "cervence",
    8: "srpna",
    9: "zari",
    10: "rijna",
    11: "listopadu",
    12: "prosince",
}


def _date_variants(value: date) -> set[str]:
    month_name = _CZECH_MONTHS_GENITIVE[value.month]
    return {
        value.isoformat(),
        f"{value.day}.{value.month}.{value.year}",
        f"{value.day}. {value.month}. {value.year}",
        f"{value.day:02d}.{value.month:02d}.{value.year}",
        f"{value.day:02d}. {value.month:02d}. {value.year}",
        f"{value.day:02d}.{value.month:02d}.{str(value.year)[2:]}",
        f"{value.day}. {value.month}. {str(value.year)[2:]}",
        # Written-out Czech dates ("15. května 2026", "15.května 2026").
        f"{value.day}. {month_name} {value.year}",
        f"{value.day}.{month_name} {value.year}",
        f"{value.day}. {month_name}",
    }


def _date_appears_in_source(value: date, source_text: str) -> bool:
    # Strip accents so written-out month names and any accented numeric forms
    # match; numeric variants are accent-free, so this is lossless for them.
    normalized_source = re.sub(r"\s+", " ", _strip_accents(source_text))
    return any(variant in normalized_source for variant in _date_variants(value))


# Relative deadlines ("odvolání do 15 dnů od doručení", "ve lhůtě 30 dnů") have
# no absolute date in OCR, so the analyzer's computed date never matches and the
# deadline is dropped. These (soudní/cizinecká lhůty) are the most dangerous to
# miss, so the analyzer surfaces a manual-reminder tip instead of silently
# dropping them. Returns the day count when such a phrase is present.
_RELATIVE_DEADLINE_RE = re.compile(r"\b(?:do|ve lhute|lhuta|lhute)\s+(\d{1,3})\s+(?:dnu|dni|dnech|den)\b")


def detect_relative_deadline_days(source_text: str) -> int | None:
    normalized = re.sub(r"\s+", " ", _strip_accents(source_text))
    match = _RELATIVE_DEADLINE_RE.search(normalized)
    return int(match.group(1)) if match else None


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
            # An explicit expiration type IS the critical signal for a contract
            # end; only the fuzzier types need a keyword to qualify as critical.
            normalized_description = _strip_accents(d.description)
            is_critical = d.type == "expiration" or any(
                keyword in normalized_description for keyword in SMLUVY_CRITICAL_KEYWORDS
            )
            if not is_critical:
                rejected.append(f"non-critical smlouvy deadline: {d.date.isoformat()} '{d.description[:40]}'")
                continue
        kept.append(d)

    return kept, rejected


def coerce_deadline_type(value: str) -> DeadlineType:
    """Map free-form LLM output to one of our 4 valid deadline types."""
    if value in VALID_TYPES:
        return value  # type: ignore[return-value]
    return "action"  # safest fallback — neutral non-payment, non-expiration
