"""JWT auth middleware tests."""

from uuid import UUID

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.config import get_settings
from app.middleware.auth import get_current_user_id


def _make_token(secret: str, **overrides) -> str:
    import os

    issuer = f"{os.environ['SUPABASE_URL'].rstrip('/')}/auth/v1"
    payload = {
        "sub": "11111111-1111-1111-1111-111111111111",
        "aud": "authenticated",
        "exp": 9999999999,
        "iat": 1700000000,
        "iss": issuer,
        "role": "authenticated",
    }
    payload.update(overrides)
    return jwt.encode(payload, secret, algorithm="HS256")


class TestGetCurrentUserId:
    def test_valid_token(self):
        settings = get_settings()
        token = _make_token(settings.supabase_jwt_secret)
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        user_id = get_current_user_id(creds, settings)
        assert user_id == UUID("11111111-1111-1111-1111-111111111111")

    def test_expired_token(self):
        settings = get_settings()
        token = _make_token(settings.supabase_jwt_secret, exp=1)
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        with pytest.raises(HTTPException) as exc:
            get_current_user_id(creds, settings)
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail.lower()

    def test_invalid_signature(self):
        settings = get_settings()
        token = _make_token("wrong-secret")
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        with pytest.raises(HTTPException) as exc:
            get_current_user_id(creds, settings)
        assert exc.value.status_code == 401

    def test_wrong_audience(self):
        settings = get_settings()
        token = _make_token(settings.supabase_jwt_secret, aud="wrong")
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        with pytest.raises(HTTPException) as exc:
            get_current_user_id(creds, settings)
        assert exc.value.status_code == 401

    def test_missing_sub_claim(self):
        settings = get_settings()
        payload = {"aud": "authenticated", "exp": 9999999999}
        token = jwt.encode(payload, settings.supabase_jwt_secret, algorithm="HS256")
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        with pytest.raises(HTTPException) as exc:
            get_current_user_id(creds, settings)
        assert exc.value.status_code == 401

    def test_malformed_sub(self):
        settings = get_settings()
        token = _make_token(settings.supabase_jwt_secret, sub="not-a-uuid")
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        with pytest.raises(HTTPException) as exc:
            get_current_user_id(creds, settings)
        assert exc.value.status_code == 401

    def test_garbage_token(self):
        settings = get_settings()
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not.a.jwt")

        with pytest.raises(HTTPException) as exc:
            get_current_user_id(creds, settings)
        assert exc.value.status_code == 401
