import logging
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=True)


@lru_cache
def _get_jwks_client(supabase_url: str) -> PyJWKClient:
    """Cached JWKS client — public keys fetched once, cache refreshes per lib defaults."""
    jwks_url = f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    return PyJWKClient(jwks_url, cache_keys=True)


def _decode_token_payload(token: str, settings: Settings) -> dict:
    issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"
    options = {"require": ["sub", "exp", "iss"]}

    try:
        # Validate legacy symmetric tokens first.
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
            issuer=issuer,
            options=options,
        )
    except (jwt.InvalidAlgorithmError, jwt.InvalidSignatureError):
        # If signature/algorithm doesn't match HS256, try asymmetric keys.
        signing_key = _get_jwks_client(settings.supabase_url).get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
            issuer=issuer,
            options=options,
        )


def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> UUID:
    token = credentials.credentials

    try:
        payload = _decode_token_payload(token, settings)
        return UUID(payload["sub"])

    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired") from exc
    except jwt.InvalidTokenError as exc:
        logger.warning("Invalid JWT: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
    except (ValueError, KeyError) as exc:
        logger.warning("Malformed token payload: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed token payload"
        ) from exc
