"""Shared fee/poplatek guardrails used by the analyzer and chat services.

Czech "Výzva k převzetí průkazu" (card-pickup notice) forms have a checkbox
section listing possible fees (300 Kč, 1000 Kč, 2500 Kč, or "bez poplatku").
The analyzer and chat services both need to detect whether a positive fee is
actually checked on the form, and suppress any LLM output that tells the user
to "prepare stamps" when the OCR shows no such checkmark.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

FEE_TERMS: tuple[str, ...] = (
    "poplatek",
    "poplatku",
    "kolk",
    "kolku",
    "kolky",
    "uhradit",
    "zaplatit",
    "fee",
    "payment",
    "stamp",
    "оплат",
    "платеж",
    "платіж",
    "колк",
)

_DIACRITIC_FOLD = str.maketrans(
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

_CHECKED_MARK = r"(?:\[x\]|\(x\)|☒|✅|✓|✔|x)"
_POSITIVE_FEE_AMOUNT = r"(?:300|1\s*000|1000|2\s*500|2500)\s*(?:kc|kč)"
_POSITIVE_FEE_RE = re.compile(
    rf"(?:{_CHECKED_MARK}\s*{_POSITIVE_FEE_AMOUNT})|(?:{_POSITIVE_FEE_AMOUNT}\s*{_CHECKED_MARK})"
)
_NO_FEE_RE = re.compile(rf"{_CHECKED_MARK}\s*bez poplatku\s*\(0\s*(?:kc|kč)\)")


def ascii_fold(text: str) -> str:
    """Lowercase + strip the Czech diacritics we care about for keyword checks."""
    return text.lower().translate(_DIACRITIC_FOLD)


def looks_like_card_pickup_notice(doc_type: str, ocr_text: str) -> bool:
    doc_type_folded = ascii_fold(doc_type)
    ocr_folded = ascii_fold(ocr_text)
    return (
        "vyzva_k_prevzeti_prukazu" in doc_type_folded
        or "vyzva k prevzeti prukazu" in ocr_folded
        or "prevzeti prukazu o povoleni k dlouhodobemu pobytu" in ocr_folded
    )


def has_checked_positive_fee(ocr_text: str) -> bool:
    return bool(_POSITIVE_FEE_RE.search(ascii_fold(ocr_text)))


def has_checked_no_fee(ocr_text: str) -> bool:
    return bool(_NO_FEE_RE.search(ascii_fold(ocr_text)))


def should_suppress_fee_guidance(doc_type: str, ocr_text: str) -> bool:
    """True when the document is a card-pickup notice with no positive fee checked."""
    if not looks_like_card_pickup_notice(doc_type, ocr_text):
        return False
    if has_checked_no_fee(ocr_text):
        return True
    return not has_checked_positive_fee(ocr_text)


def mentions_fee(text: str) -> bool:
    folded = ascii_fold(text)
    lowered = text.lower()
    return any(term in folded or term in lowered for term in FEE_TERMS)


def filter_fee_items(items: Iterable[object]) -> list[str]:
    """Drop any item whose stringified form mentions a fee/stamp."""
    return [str(item) for item in items if not mentions_fee(str(item))]
