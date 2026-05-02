from pathlib import Path

from scripts.load_knowledge import (
    build_chunks,
    clean_markdown_for_embedding,
    discover_documents,
    parse_frontmatter,
)


def test_parse_frontmatter_with_lists():
    raw = """---
category: cizinecka
language: cs
source_urls:
  - "https://example.com/a"
  - "https://example.com/b"
enabled: true
---
# Title

Body
"""

    metadata, body = parse_frontmatter(raw)

    assert metadata["category"] == "cizinecka"
    assert metadata["language"] == "cs"
    assert metadata["source_urls"] == ["https://example.com/a", "https://example.com/b"]
    assert metadata["enabled"] is True
    assert body.startswith("# Title")


def test_clean_markdown_removes_crawler_noise_but_keeps_article_links():
    body = """# Example

Source: https://ipc.gov.cz/example/

# Example

[Useful link](https://ipc.gov.cz/useful/)

[image: foo]

###

Načítavání obsahu
"""

    cleaned = clean_markdown_for_embedding(body)

    assert cleaned.count("# Example") == 1
    assert "Source:" not in cleaned
    assert "Načítavání obsahu" not in cleaned
    assert "[image:" not in cleaned
    assert "###" not in cleaned
    assert "[Useful link](https://ipc.gov.cz/useful/)" in cleaned


def test_discover_documents_uses_allowlist_and_metadata(tmp_path: Path):
    root = tmp_path / "knowledge"
    czech = root / "cizinecka" / "Czech"
    ignored = root / "cizinecka" / "_quarantine"
    czech.mkdir(parents=True)
    ignored.mkdir(parents=True)
    (czech / "doc.md").write_text(
        """---
category: cizinecka
language: cs
source_url: https://ipc.gov.cz/doc/
---
# Doklad

Text dokumentu.
""",
        encoding="utf-8",
    )
    (ignored / "bad.md").write_text("# Bad\n\nShould not load.", encoding="utf-8")

    documents = discover_documents("cizinecka", root, ("Czech",))

    assert len(documents) == 1
    assert documents[0].metadata["category"] == "cizinecka"
    assert documents[0].metadata["language"] == "cs"
    assert documents[0].metadata["source_type"] == "official_page"
    assert documents[0].metadata["source_dir"] == "Czech"
    assert documents[0].metadata["source_url"] == "https://ipc.gov.cz/doc/"
    assert documents[0].metadata["title"] == "Doklad"


def test_build_chunks_preserves_document_metadata(tmp_path: Path):
    root = tmp_path / "knowledge"
    cards = root / "cizinecka" / "requirement_cards"
    cards.mkdir(parents=True)
    (cards / "card.md").write_text(
        """---
category: cizinecka
language: cs
source_type: requirement_card
title: "Doklad o ubytování"
---
# Doklad o ubytování

## Kdy se to objevuje

""" + ("Text. " * 400),
        encoding="utf-8",
    )

    documents = discover_documents("cizinecka", root, ("requirement_cards",))
    chunks = build_chunks(documents, chunk_chars=500, overlap_chars=80)

    assert len(chunks) > 1
    assert all(chunk.metadata["source_type"] == "requirement_card" for chunk in chunks)
    assert all(chunk.metadata["chunk_count"] == len(chunks) for chunk in chunks)
    assert [chunk.metadata["chunk_index"] for chunk in chunks] == list(range(len(chunks)))
