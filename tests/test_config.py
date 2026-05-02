"""Settings validation tests."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def _base_settings(**overrides):
    values = {
        "openai_api_key": "test-key",
        "supabase_url": "https://test.supabase.co",
        "supabase_service_key": "test-service-key",
        "supabase_jwt_secret": "test-jwt-secret",
        "environment": "development",
    }
    values.update(overrides)
    return values


def test_allows_wildcard_origins_outside_production():
    settings = Settings(**_base_settings(allowed_origins=["*"]))
    assert settings.allowed_origins == ["*"]


def test_rejects_wildcard_origins_in_production():
    with pytest.raises(ValidationError):
        Settings(**_base_settings(environment="production", allowed_origins=["*"]))


def test_parses_comma_separated_origins():
    settings = Settings(
        **_base_settings(
            environment="production",
            allowed_origins="https://app.example, https://admin.example",
        )
    )
    assert settings.allowed_origins == ["https://app.example", "https://admin.example"]


def test_rag_threshold_defaults_to_retrieval_friendly_value():
    settings = Settings(**_base_settings())
    assert settings.rag_similarity_threshold == 0.50
