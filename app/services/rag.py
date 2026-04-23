import logging

from app.config import get_settings
from app.models.schemas import DocumentCategory
from app.services.embeddings import embed_text
from supabase import AsyncClient, create_async_client

logger = logging.getLogger(__name__)

_supabase_client: AsyncClient | None = None


async def init_supabase() -> AsyncClient:
    """Initialise the singleton. Call from app lifespan."""
    global _supabase_client
    if _supabase_client is None:
        settings = get_settings()
        _supabase_client = await create_async_client(
            settings.supabase_url,
            settings.supabase_service_key,
        )
        logger.info("Supabase client initialised")
    return _supabase_client


async def get_supabase() -> AsyncClient:
    """Lazy getter — falls back to init if lifespan didn't run (e.g. tests)."""
    if _supabase_client is None:
        return await init_supabase()
    return _supabase_client


async def close_supabase() -> None:
    """Close on shutdown."""
    global _supabase_client
    if _supabase_client is not None:
        # supabase-py AsyncClient doesn't require explicit close, but we drop the ref
        _supabase_client = None
        logger.info("Supabase client released")


async def retrieve_chunks(ocr_text: str, category: DocumentCategory) -> list[str]:
    """Cosine-similarity search with category filter via pgvector RPC."""
    if not ocr_text.strip():
        return []

    settings = get_settings()
    embedding = await embed_text(ocr_text)
    supabase = await get_supabase()

    result = await supabase.rpc(
        "match_knowledge_chunks",
        {
            "query_embedding": embedding,
            "filter_category": category,
            "match_threshold": settings.rag_similarity_threshold,
            "match_count": settings.rag_top_k,
        },
    ).execute()

    chunks: list[str] = [row["content"] for row in (result.data or [])]
    logger.info("RAG returned %d chunks for category=%s", len(chunks), category)
    return chunks
