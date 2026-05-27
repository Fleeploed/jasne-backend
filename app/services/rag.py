import logging
import re

from app.config import get_settings
from app.models.schemas import DocumentCategory
from app.services.embeddings import embed_text
from supabase import AsyncClient, create_async_client

logger = logging.getLogger(__name__)

_supabase_client: AsyncClient | None = None

_CHAT_KEYWORD_STOPWORDS = {
    "address",
    "hours",
    "office",
    "open",
    "opening",
    "pracovni",
    "pracovní",
    "hodiny",
    "oteviraci",
    "otevírací",
    "doba",
    "kde",
    "kam",
    "jaky",
    "jaký",
    "jake",
    "jaké",
    "what",
    "where",
    "when",
}


def _is_searchable_keyword(term: str) -> bool:
    folded = term.casefold()
    has_ascii_letter = any(ch.isascii() and ch.isalpha() for ch in term)
    has_digit = any(ch.isdigit() for ch in term)

    if not has_ascii_letter and not has_digit:
        return False
    if folded in _CHAT_KEYWORD_STOPWORDS:
        return False
    return len(term) >= 4 or (has_digit and "/" in term)


def _keyword_rank(term: str) -> tuple[int, int]:
    has_digit = any(ch.isdigit() for ch in term)
    has_slash = "/" in term
    has_diacritic = any((not ch.isascii()) and ch.isalpha() for ch in term)
    if has_digit and has_slash:
        bucket = 0
    elif has_diacritic:
        bucket = 1
    elif has_digit:
        bucket = 2
    else:
        bucket = 3
    return (bucket, -len(term))


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


async def _retrieve_vector_chunks(query_text: str, category: DocumentCategory) -> list[str]:
    """Cosine-similarity search with category filter via pgvector RPC."""
    if not query_text.strip():
        return []

    settings = get_settings()
    embedding = await embed_text(query_text)
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
    return chunks


async def retrieve_chunks(ocr_text: str, category: DocumentCategory) -> list[str]:
    """Hybrid scan retrieval.

    Vector search finds broad process context. Keyword search is needed for
    exact office addresses like "Žukovského 888/2", where hours/contact chunks
    should win over generic immigration chunks.
    """
    if not ocr_text.strip():
        return []

    settings = get_settings()
    keyword_chunks = await retrieve_keyword_chunks(ocr_text, category, match_count=settings.rag_top_k)
    vector_chunks = await _retrieve_vector_chunks(ocr_text, category)

    chunks: list[str] = []
    seen: set[str] = set()
    for chunk in [*keyword_chunks, *vector_chunks]:
        key = _chunk_dedupe_key(chunk)
        if key in seen:
            continue
        seen.add(key)
        chunks.append(chunk)
        if len(chunks) >= settings.rag_top_k:
            break

    logger.info("RAG returned %d chunks for category=%s", len(chunks), category)
    return chunks


def _extract_keyword_terms(text: str, *, limit: int = 8) -> list[str]:
    """Extract exact-match terms for chat RAG fallback.

    Vector search is good for process questions, but exact addresses like
    "Voroněžská 144/20" are often better found lexically.
    """
    terms: list[str] = []
    seen: set[str] = set()
    for raw in re.findall(r"[\wÀ-ž/.-]+", text, flags=re.UNICODE):
        term = raw.strip(".,;:!?()[]{}\"'`").strip()
        if not term:
            continue
        folded = term.casefold()
        if folded in seen:
            continue
        if not _is_searchable_keyword(term):
            continue
        terms.append(term)
        seen.add(folded)

    return sorted(terms, key=_keyword_rank)[:limit]


def _escape_ilike(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _snippet_around_term(content: str, term: str, *, window: int = 900) -> str:
    match = re.search(re.escape(term), content, flags=re.IGNORECASE)
    if not match:
        return content

    start = max(0, match.start() - window)
    end = min(len(content), match.end() + window)
    snippet = content[start:end].strip()
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(content):
        snippet = f"{snippet}..."
    return snippet


def _chunk_dedupe_key(chunk: str) -> str:
    chunk = chunk.strip()
    if chunk.startswith("..."):
        chunk = chunk[3:].lstrip()
    if chunk.endswith("..."):
        chunk = chunk[:-3].rstrip()
    return re.sub(r"\s+", " ", chunk).casefold()[:500]


def _language_rank(metadata: dict, preferred_language: str | None) -> tuple[int, str]:
    language = str(metadata.get("language") or "")
    if preferred_language and language == preferred_language:
        return (0, language)
    if language == "cs":
        return (1, language)
    if language == "en":
        return (2, language)
    return (3, language)


async def retrieve_keyword_chunks(
    query_text: str,
    category: DocumentCategory,
    *,
    match_count: int = 5,
    preferred_language: str | None = None,
) -> list[str]:
    """Keyword fallback for exact addresses, office names, and identifiers."""
    terms = _extract_keyword_terms(query_text)
    if not terms:
        return []

    supabase = await get_supabase()
    chunks: list[str] = []
    seen: set[str] = set()

    for term in terms:
        if len(chunks) >= match_count:
            break

        result = (
            await supabase.table("knowledge_chunks")
            .select("content,metadata")
            .eq("metadata->>category", category)
            .ilike("content", f"%{_escape_ilike(term)}%")
            .order("id", desc=True)
            .limit(max(4, (match_count - len(chunks)) * 4))
            .execute()
        )
        rows = sorted(
            result.data or [],
            key=lambda row: _language_rank(row.get("metadata") or {}, preferred_language),
        )
        for row in rows:
            content = _snippet_around_term(str(row.get("content") or ""), term).strip()
            if not content or content in seen:
                continue
            seen.add(content)
            chunks.append(content)
            if len(chunks) >= match_count:
                break

    logger.info("Keyword RAG returned %d chunks for category=%s terms=%s", len(chunks), category, terms)
    return chunks


async def retrieve_chat_chunks(
    question: str, category: DocumentCategory, preferred_language: str | None = None
) -> list[str]:
    """Hybrid retrieval for follow-up chat questions."""
    if not question.strip() or not category:
        return []

    settings = get_settings()
    keyword_chunks = await retrieve_keyword_chunks(
        question,
        category,
        match_count=settings.rag_top_k,
        preferred_language=preferred_language,
    )
    vector_chunks = await _retrieve_vector_chunks(question, category)

    chunks: list[str] = []
    seen: set[str] = set()
    for chunk in [*keyword_chunks, *vector_chunks]:
        key = _chunk_dedupe_key(chunk)
        if key in seen:
            continue
        seen.add(key)
        chunks.append(chunk)
        if len(chunks) >= settings.rag_top_k:
            break

    logger.info("Chat RAG returned %d chunks for category=%s", len(chunks), category)
    return chunks
