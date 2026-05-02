"""Load local markdown knowledge files into Supabase pgvector.

Default scope is the prepared cizinecka corpus. The script intentionally uses
an allowlist of folders so review/quarantine material is never ingested by
accident.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from supabase import AsyncClient, create_async_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

DEFAULT_CATEGORY = "cizinecka"
DEFAULT_INCLUDE_DIRS = (
    "Czech",
    "English",
    "legal_basis",
    "legal_cards",
    "requirement_cards",
    "procedure_cards",
)

MIN_CHUNK_CHARS = 350
DEFAULT_CHUNK_CHARS = 3200
DEFAULT_OVERLAP_CHARS = 350
DEFAULT_BATCH_SIZE = 64


@dataclass(frozen=True)
class KnowledgeDocument:
    path: Path
    relative_path: str
    content: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class KnowledgeChunk:
    content: str
    metadata: dict[str, Any]


def parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Parse the small YAML subset used by the local markdown corpus."""
    if not raw.startswith("---\n"):
        return {}, raw

    end = raw.find("\n---", 4)
    if end == -1:
        return {}, raw

    block = raw[4:end].strip("\n")
    body = raw[end + len("\n---") :].lstrip("\n")
    metadata: dict[str, Any] = {}
    current_key: str | None = None

    for line in block.splitlines():
        if not line.strip():
            continue

        if line.startswith("  - ") and current_key:
            value = _parse_scalar(line[4:].strip())
            existing = metadata.setdefault(current_key, [])
            if isinstance(existing, list):
                existing.append(value)
            continue

        if ":" not in line:
            current_key = None
            continue

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        if value:
            metadata[key] = _parse_scalar(value)
            current_key = None
        else:
            metadata[key] = []

    return metadata, body


def _parse_scalar(value: str) -> Any:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value.strip('"')
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value


def clean_markdown_for_embedding(body: str) -> str:
    """Remove crawler boilerplate while preserving meaningful article links."""
    lines: list[str] = []
    seen_h1: set[str] = set()

    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            lines.append("")
            continue

        if stripped.startswith("Source: http"):
            continue
        if stripped == "Načítavání obsahu":
            continue
        if stripped.startswith("[image:") and stripped.endswith("]"):
            continue
        if stripped == "###":
            continue

        if stripped.startswith("# "):
            normalized = stripped.casefold()
            if normalized in seen_h1:
                continue
            seen_h1.add(normalized)

        lines.append(line)

    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def document_title(metadata: dict[str, Any], body: str, path: Path) -> str:
    for key in ("title", "law_title"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    match = re.search(r"^#\s+(.+)$", body, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()

    return path.stem.replace("-", " ")


def discover_documents(
    category: str,
    knowledge_root: Path,
    include_dirs: tuple[str, ...],
) -> list[KnowledgeDocument]:
    category_root = knowledge_root / category
    documents: list[KnowledgeDocument] = []

    for include_dir in include_dirs:
        source_dir = category_root / include_dir
        if not source_dir.exists():
            logger.warning("Skipping missing knowledge dir: %s", source_dir)
            continue

        for path in sorted(source_dir.glob("*.md")):
            raw = path.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter(raw)
            cleaned = clean_markdown_for_embedding(body)
            if not cleaned:
                logger.warning("Skipping empty document: %s", path)
                continue

            relative_path = path.relative_to(knowledge_root).as_posix()
            source_urls = _source_urls(frontmatter)
            source_type = str(frontmatter.get("source_type") or _source_type_from_dir(include_dir)).strip()
            language = str(frontmatter.get("language") or _language_from_dir(include_dir)).strip()
            title = document_title(frontmatter, cleaned, path)

            metadata: dict[str, Any] = {
                "category": category,
                "language": language,
                "source_type": source_type,
                "source_dir": include_dir,
                "source_url": source_urls[0] if source_urls else None,
                "source_urls": source_urls,
                "title": title,
                "document_id": _document_id(category, relative_path),
                "relative_path": relative_path,
                "content_sha256": _sha256(cleaned),
            }

            for key in (
                "last_reviewed",
                "law_number",
                "law_title",
                "jurisdiction",
                "related_laws",
                "related_topics",
            ):
                if key in frontmatter:
                    metadata[key] = frontmatter[key]

            documents.append(
                KnowledgeDocument(
                    path=path,
                    relative_path=relative_path,
                    content=cleaned,
                    metadata=metadata,
                )
            )

    return documents


def _source_urls(frontmatter: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("source_url", "official_source_url", "reference_source_url"):
        value = frontmatter.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            urls.append(value)

    for key in ("source_urls", "reference_source_urls"):
        values = frontmatter.get(key)
        if isinstance(values, list):
            urls.extend(
                value
                for value in values
                if isinstance(value, str) and value.startswith(("http://", "https://"))
            )

    return list(dict.fromkeys(urls))


def _language_from_dir(include_dir: str) -> str:
    return {
        "Czech": "cs",
        "English": "en",
        "Ukrainian": "uk",
    }.get(include_dir, "cs")


def _source_type_from_dir(include_dir: str) -> str:
    return {
        "Czech": "official_page",
        "English": "official_page",
        "legal_basis": "legal_basis",
        "legal_cards": "legal_card",
        "requirement_cards": "requirement_card",
        "procedure_cards": "procedure_card",
    }.get(include_dir, include_dir)


def _document_id(category: str, relative_path: str) -> str:
    return _sha256(f"{category}:{relative_path}")[:24]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def chunk_document(
    document: KnowledgeDocument,
    *,
    chunk_chars: int,
    overlap_chars: int,
) -> list[KnowledgeChunk]:
    parts = _split_markdown_sections(document.content)
    chunks: list[str] = []
    current = ""

    for part in parts:
        if not current:
            current = part
            continue

        if len(current) + len(part) + 2 <= chunk_chars:
            current = f"{current}\n\n{part}"
            continue

        chunks.extend(_split_oversized_text(current, chunk_chars, overlap_chars))
        current = _overlap_tail(current, overlap_chars)
        current = f"{current}\n\n{part}".strip() if current else part

    if current:
        chunks.extend(_split_oversized_text(current, chunk_chars, overlap_chars))

    result: list[KnowledgeChunk] = []
    for index, chunk in enumerate(_merge_tiny_chunks(chunks, chunk_chars)):
        content = chunk.strip()
        if not content:
            continue
        metadata = {
            **document.metadata,
            "chunk_index": index,
            "chunk_count": 0,  # filled below
            "chunk_sha256": _sha256(content),
        }
        result.append(KnowledgeChunk(content=content, metadata=metadata))

    chunk_count = len(result)
    return [
        KnowledgeChunk(
            content=chunk.content,
            metadata={**chunk.metadata, "chunk_count": chunk_count},
        )
        for chunk in result
    ]


def _split_markdown_sections(text: str) -> list[str]:
    sections: list[str] = []
    current: list[str] = []

    for line in text.splitlines():
        if line.startswith("## ") and current:
            sections.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)

    if current:
        sections.append("\n".join(current).strip())

    return [section for section in sections if section]


def _split_oversized_text(text: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= chunk_chars:
        return [text]

    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        if len(paragraph) > chunk_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_paragraph(paragraph, chunk_chars, overlap_chars))
            continue

        if not current:
            current = paragraph
        elif len(current) + len(paragraph) + 2 <= chunk_chars:
            current = f"{current}\n\n{paragraph}"
        else:
            chunks.append(current)
            tail = _overlap_tail(current, overlap_chars)
            current = f"{tail}\n\n{paragraph}".strip() if tail else paragraph

    if current:
        chunks.append(current)

    return chunks


def _split_long_paragraph(text: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_chars, len(text))
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(0, end - overlap_chars)
    return chunks


def _overlap_tail(text: str, overlap_chars: int) -> str:
    if overlap_chars <= 0 or len(text) <= overlap_chars:
        return ""

    tail = text[-overlap_chars:]
    boundary = max(tail.find("\n\n"), tail.find(". "))
    if boundary > 0:
        tail = tail[boundary:].strip()
    return tail.strip()


def _merge_tiny_chunks(chunks: list[str], chunk_chars: int) -> list[str]:
    merged: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue

        if (
            merged
            and len(chunk) < MIN_CHUNK_CHARS
            and len(merged[-1]) + len(chunk) + 2 <= chunk_chars
        ):
            merged[-1] = f"{merged[-1]}\n\n{chunk}"
        else:
            merged.append(chunk)
    return merged


def build_chunks(
    documents: list[KnowledgeDocument],
    *,
    chunk_chars: int,
    overlap_chars: int,
    limit: int | None = None,
) -> list[KnowledgeChunk]:
    selected = documents[:limit] if limit else documents
    chunks: list[KnowledgeChunk] = []
    for document in selected:
        chunks.extend(
            chunk_document(
                document,
                chunk_chars=chunk_chars,
                overlap_chars=overlap_chars,
            )
        )
    return chunks


async def embed_batch(
    client: AsyncOpenAI,
    texts: list[str],
    *,
    model: str,
    dimensions: int,
) -> list[list[float]]:
    response = await client.embeddings.create(
        model=model,
        input=texts,
        dimensions=dimensions,
    )
    return [item.embedding for item in response.data]


async def reset_category(supabase: AsyncClient, category: str) -> None:
    logger.info("Deleting existing knowledge chunks for category=%s", category)
    await (
        supabase.table("knowledge_chunks")
        .delete()
        .eq("metadata->>category", category)
        .execute()
    )


async def upload_chunks(
    *,
    supabase: AsyncClient,
    openai_client: AsyncOpenAI,
    chunks: list[KnowledgeChunk],
    batch_size: int,
) -> None:
    from app.config import get_settings

    settings = get_settings()
    total = len(chunks)
    ingested_at = datetime.now(UTC).isoformat()

    for start in range(0, total, batch_size):
        batch = chunks[start : start + batch_size]
        logger.info("Embedding/uploading chunks %d-%d of %d", start + 1, start + len(batch), total)
        embeddings = await embed_batch(
            openai_client,
            [chunk.content for chunk in batch],
            model=settings.openai_embedding_model,
            dimensions=settings.openai_embedding_dims,
        )
        rows = [
            {
                "content": chunk.content,
                "metadata": {
                    **chunk.metadata,
                    "embedding_model": settings.openai_embedding_model,
                    "embedding_dims": settings.openai_embedding_dims,
                    "ingested_at": ingested_at,
                },
                "embedding": embedding,
            }
            for chunk, embedding in zip(batch, embeddings, strict=True)
        ]
        await supabase.table("knowledge_chunks").insert(rows).execute()


def summarize(documents: list[KnowledgeDocument], chunks: list[KnowledgeChunk]) -> dict[str, Any]:
    source_types: dict[str, int] = {}
    languages: dict[str, int] = {}
    for document in documents:
        source_types[str(document.metadata.get("source_type"))] = (
            source_types.get(str(document.metadata.get("source_type")), 0) + 1
        )
        languages[str(document.metadata.get("language"))] = (
            languages.get(str(document.metadata.get("language")), 0) + 1
        )

    chunk_source_types: dict[str, int] = {}
    for chunk in chunks:
        source_type = str(chunk.metadata.get("source_type"))
        chunk_source_types[source_type] = chunk_source_types.get(source_type, 0) + 1

    return {
        "documents": len(documents),
        "chunks": len(chunks),
        "document_languages": languages,
        "document_source_types": source_types,
        "chunk_source_types": chunk_source_types,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load markdown knowledge into Supabase RAG.")
    parser.add_argument("--category", default=DEFAULT_CATEGORY)
    parser.add_argument("--knowledge-root", type=Path, default=Path("knowledge"))
    parser.add_argument(
        "--include-dir",
        action="append",
        dest="include_dirs",
        help="Allowed category subdirectory. Repeatable. Defaults to cizinecka allowlist.",
    )
    parser.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS)
    parser.add_argument("--overlap-chars", type=int, default=DEFAULT_OVERLAP_CHARS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--limit", type=int, default=None, help="Limit documents for smoke tests.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and chunk without API calls.")
    parser.add_argument(
        "--reset-category",
        action="store_true",
        help="Delete existing chunks for the category before inserting.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    include_dirs = tuple(args.include_dirs or DEFAULT_INCLUDE_DIRS)
    documents = discover_documents(args.category, args.knowledge_root, include_dirs)
    chunks = build_chunks(
        documents,
        chunk_chars=args.chunk_chars,
        overlap_chars=args.overlap_chars,
        limit=args.limit,
    )
    print(json.dumps(summarize(documents[: args.limit] if args.limit else documents, chunks), indent=2))

    if args.dry_run:
        return

    from app.config import get_settings

    settings = get_settings()
    supabase = await create_async_client(settings.supabase_url, settings.supabase_service_key)
    openai_client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )

    try:
        if args.reset_category:
            await reset_category(supabase, args.category)
        await upload_chunks(
            supabase=supabase,
            openai_client=openai_client,
            chunks=chunks,
            batch_size=args.batch_size,
        )
    finally:
        await openai_client.close()


if __name__ == "__main__":
    asyncio.run(main())
