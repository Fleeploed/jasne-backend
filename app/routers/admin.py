"""Admin-only endpoints.

These proxy server-to-server calls that the static Help admin (help-admin/
index.html) cannot make directly from the browser due to CORS — most
notably DeepL Free, which does not return `Access-Control-Allow-Origin`.

Auth model: the request must carry a JWT for a user whose `profiles.role`
column equals `'admin'`. We do NOT rely on RLS here — the FastAPI service
uses the service_role key which bypasses RLS, so we check the role in code.
"""

from __future__ import annotations

import logging
import os
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.middleware.auth import get_current_user_id
from app.services.rag import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

# DeepL Free keys end in ":fx" and hit api-free.deepl.com. Pro keys use api.deepl.com.
DEEPL_FREE_ENDPOINT = "https://api-free.deepl.com/v2"
DEEPL_PRO_ENDPOINT = "https://api.deepl.com/v2"

DeepLLang = Literal["CS", "RU", "UK", "EN-US", "EN-GB", "EN", "DE", "FR", "ES", "IT"]


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


async def require_admin(user_id: UUID) -> None:
    """Raise 403 if the caller's profile.role is not 'admin'."""
    supabase = await get_supabase()
    res = await supabase.table("profiles").select("role").eq("id", str(user_id)).single().execute()
    role = (res.data or {}).get("role")
    if role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")


def _deepl_key() -> str:
    """Read DEEPL_API_KEY from env. 503 if not configured, so the admin UI can show a helpful message."""
    key = os.environ.get("DEEPL_API_KEY", "").strip()
    if not key or len(key) < 10:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DEEPL_API_KEY not configured on the backend",
        )
    return key


def _deepl_endpoint(key: str) -> str:
    return DEEPL_FREE_ENDPOINT if key.endswith(":fx") else DEEPL_PRO_ENDPOINT


# ---------------------------------------------------------------------------
# /admin/translate
# ---------------------------------------------------------------------------


class TranslateRequest(BaseModel):
    source_lang: DeepLLang
    target_lang: DeepLLang
    texts: list[str] = Field(min_length=1, max_length=200)


class TranslateResponse(BaseModel):
    translations: list[str]


@router.post("/translate", response_model=TranslateResponse)
async def translate(
    body: TranslateRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> TranslateResponse:
    await require_admin(user_id)
    key = _deepl_key()

    # DeepL accepts EN-US/EN-GB only as target_lang. For source_lang it
    # demands the base code. Normalise here so callers don't have to care.
    src = body.source_lang.split("-")[0]

    # Auth via header (not auth_key body param). And use `content=` with
    # an explicit urlencoded body — httpx.AsyncClient.post(data=list_of_tuples)
    # raises "Attempted to send a sync request with an AsyncClient instance"
    # in 0.28.x; the `content=` path is the supported async route.
    params: list[tuple[str, str]] = [
        ("source_lang", src),
        ("target_lang", body.target_lang),
        ("preserve_formatting", "1"),
    ]
    for t in body.texts:
        params.append(("text", t))
    body_str = urlencode(params)

    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(
            f"{_deepl_endpoint(key)}/translate",
            content=body_str,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"DeepL-Auth-Key {key}",
            },
        )

    if res.status_code != 200:
        logger.warning("DeepL translate failed: %d %s", res.status_code, res.text[:200])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"DeepL {res.status_code}: {res.text[:200]}",
        )
    data = res.json()
    return TranslateResponse(translations=[t["text"] for t in data["translations"]])


# ---------------------------------------------------------------------------
# /admin/translate/usage
# ---------------------------------------------------------------------------


class UsageResponse(BaseModel):
    character_count: int
    character_limit: int


@router.get("/translate/usage", response_model=UsageResponse)
async def translate_usage(
    user_id: UUID = Depends(get_current_user_id),
) -> UsageResponse:
    await require_admin(user_id)
    key = _deepl_key()
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(
            f"{_deepl_endpoint(key)}/usage",
            headers={"Authorization": f"DeepL-Auth-Key {key}"},
        )
    if res.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"DeepL usage {res.status_code}",
        )
    return UsageResponse(**res.json())
