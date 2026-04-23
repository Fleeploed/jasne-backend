"""Tests for embedding service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.embeddings import embed_text


class TestEmbedText:
    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(
            return_value=MagicMock(data=[MagicMock(embedding=[0.1, 0.2, 0.3])])
        )
        monkeypatch.setattr("app.services.embeddings.get_openai_client", lambda: mock_client)

        result = await embed_text("hello")
        assert result == [0.1, 0.2, 0.3]
        mock_client.embeddings.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_text_truncation(self, monkeypatch):
        from app.config import get_settings

        captured = {}

        async def fake_create(**kwargs):
            captured["input"] = kwargs["input"]
            return MagicMock(data=[MagicMock(embedding=[0.0] * 1536)])

        mock_client = MagicMock()
        mock_client.embeddings.create = fake_create
        monkeypatch.setattr("app.services.embeddings.get_openai_client", lambda: mock_client)

        max_chars = get_settings().embedding_max_chars
        long_text = "x" * (max_chars + 1000)

        await embed_text(long_text)

        assert len(captured["input"]) == max_chars

    @pytest.mark.asyncio
    async def test_short_text_not_truncated(self, monkeypatch):
        captured = {}

        async def fake_create(**kwargs):
            captured["input"] = kwargs["input"]
            return MagicMock(data=[MagicMock(embedding=[0.0] * 1536)])

        mock_client = MagicMock()
        mock_client.embeddings.create = fake_create
        monkeypatch.setattr("app.services.embeddings.get_openai_client", lambda: mock_client)

        await embed_text("short text")
        assert captured["input"] == "short text"
