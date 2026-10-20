from __future__ import annotations

import asyncio
import random
import unicodedata
from typing import Literal

from pydantic import ValidationError

from app.config import get_settings
from app.models.schemas import OampStatusCheckRequest
from app.services.oamp_status import OampStatusError, check_oamp_status

OampTrackedStatus = Literal["probiha", "schvaleno", "zamitnuto", "not_found", "error"]


async def check_status(cislo_jednaci: str) -> OampTrackedStatus:
    """Return the compact status used by automatic OAMP tracking."""
    try:
        payload = OampStatusCheckRequest(case_number=cislo_jednaci, language="cs")
    except (ValidationError, ValueError):
        return "error"

    settings = get_settings()
    user_agents = [agent for agent in settings.oamp_user_agents if agent.strip()]
    user_agent = random.choice(user_agents) if user_agents else None

    try:
        response = await asyncio.to_thread(check_oamp_status, payload, user_agent=user_agent)
    except OampStatusError:
        return "error"
    except Exception:
        return "error"

    return _classify_message(response.message)


def _classify_message(message: str) -> OampTrackedStatus:
    normalized = _normalize_for_match(message)
    if "nebylo nalezeno" in normalized or "nenalezen" in normalized or "not found" in normalized:
        return "not_found"
    if "predbezne vyhodnoceno kladne" in normalized or "preliminarily assessed positively" in normalized:
        return "schvaleno"
    if (
        "bylo nepovoleno" in normalized
        or "nepovol" in normalized
        or "zamit" in normalized
        or "nevyhov" in normalized
        or "zastavil" in normalized
        or "rejected" in normalized
        or "have been closed" in normalized
    ):
        return "zamitnuto"
    if "zpracovava se" in normalized or "v rizeni" in normalized or "being processed" in normalized:
        return "probiha"
    return "error"


def _normalize_for_match(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.split()).lower()
