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
    """Dispatch by the alg claim in the unverified header.

    Symmetric (HS256) → verify with shared secret.
    Asymmetric (ES256/RS256) → fetch matching key from Supabase JWKS.

    We dispatch on alg rather than try-HS-then-JWKS because a bad-signature
    HS256 token would otherwise fall through to a JWKS lookup, producing a
    network error instead of a clean 401.
    """
    issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"
    options = {"require": ["sub", "exp", "iss"]}

    algorithm = jwt.get_unverified_header(token).get("alg", "HS256")

    if algorithm == "HS256":
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
            issuer=issuer,
            options=options,
        )

    # Asymmetric — newer Supabase projects sign with ES256 (or RS256).
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
