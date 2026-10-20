"""Tests for scan-side RAG helpers."""

from app.services.ocr import PAGE_SEPARATOR
from app.services.rag import _vector_query_text


class TestVectorQueryText:
    def test_short_document_passes_through(self):
        assert _vector_query_text("Platební výměr č. 123") == "Platební výměr č. 123"

    def test_long_document_truncated_to_first_two_pages(self):
        pages = [f"Strana {i}" for i in range(1, 11)]
        text = PAGE_SEPARATOR.join(pages)
        result = _vector_query_text(text)
        assert result == f"Strana 1{PAGE_SEPARATOR}Strana 2"
        assert "Strana 3" not in result
