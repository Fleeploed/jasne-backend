"""Deadline validation — last line of defense against hallucinated deadlines.

Skill: „Одна галлюцинация убивает доверие". The analyzer LLM is instructed
to only use OCR-derived dates, but we still strip out anything that:
  - is in the past (we won't push-notify about expired deadlines)
  - is unreasonably far in the future (>3 years suggests hallucination)
  - has an unknown/invalid type
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from app.models.schemas import DeadlineItem, DeadlineType

logger = logging.getLogger(__name__)

# Skill: "<3 лет" — anything beyond looks made-up.
MAX_DEADLINE_HORIZON: timedelta = timedelta(days=3 * 365)

VALID_TYPES: frozenset[str] = frozenset({"payment", "action", "response", "expiration"})


def filter_valid_deadlines(
    deadlines: list[DeadlineItem], today: date | None = None
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
        kept.append(d)

    return kept, rejected


def coerce_deadline_type(value: str) -> DeadlineType:
    """Map free-form LLM output to one of our 4 valid deadline types."""
    if value in VALID_TYPES:
        return value  # type: ignore[return-value]
    return "action"  # safest fallback — neutral non-payment, non-expiration
