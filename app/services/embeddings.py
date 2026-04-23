import logging

from openai import AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)

_openai_client: AsyncOpenAI | None = None


def get_openai_client() -> AsyncOpenAI:
    """Singleton AsyncOpenAI with built-in timeout & retries."""
    global _openai_client
    if _openai_client is None:
        settings = get_settings()
        _openai_client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
        )
    return _openai_client


async def close_openai_client() -> None:
    """Close the singleton on shutdown."""
    global _openai_client
    if _openai_client is not None:
        await _openai_client.close()
        _openai_client = None


async def embed_text(text: str) -> list[float]:
    settings = get_settings()
    # text-embedding-3 models support ~8k tokens; cap input defensively
    if len(text) > settings.embedding_max_chars:
        logger.warning(
            "Truncating embedding input from %d to %d chars", len(text), settings.embedding_max_chars
        )
        text = text[: settings.embedding_max_chars]

    client = get_openai_client()
    response = await client.embeddings.create(
        model=settings.openai_embedding_model,
        input=text,
        dimensions=settings.openai_embedding_dims,
    )
    return response.data[0].embedding
