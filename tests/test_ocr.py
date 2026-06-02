"""Tests for app.services.ocr — Azure Document Intelligence integration."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import ocr as ocr_module


class _FakeAnalyzeResult:
    def __init__(self, content: str, page_count: int = 1, language: str = "cs"):
        self.content = content
        self.pages = [SimpleNamespace(page_number=i + 1) for i in range(page_count)]
        self.languages = [SimpleNamespace(locale=language, confidence=0.95)]


def _patch_analyze(monkeypatch, results: list[_FakeAnalyzeResult | None | Exception]):
    """Patch the per-URL Azure DI call. `results` is consumed in order."""
    iterator = iter(results)

    async def fake_analyze(client_kwargs: dict, url: str):
        item = next(iterator)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(ocr_module, "_analyze_single_url", fake_analyze)


class TestExtractText:
    @pytest.mark.asyncio
    async def test_extract_text_success(self, monkeypatch):
        _patch_analyze(monkeypatch, [_FakeAnalyzeResult("Hello world.", page_count=1)])
        result = await ocr_module.extract_text(["https://fake/page1.jpg"])
        assert result.ocr_text == "Hello world."
        assert result.page_count == 1
        assert result.document_language == "cs"

    @pytest.mark.asyncio
    async def test_extract_text_empty_response(self, monkeypatch):
        _patch_analyze(monkeypatch, [_FakeAnalyzeResult("", page_count=1)])
        result = await ocr_module.extract_text(["https://fake/page1.jpg"])
        assert result.ocr_text == ""
        assert result.page_count == 1
        assert result.document_language == "cs"

    @pytest.mark.asyncio
    async def test_extract_text_multiple_pages(self, monkeypatch):
        _patch_analyze(
            monkeypatch,
            [
                _FakeAnalyzeResult("Page one text.", page_count=1),
                _FakeAnalyzeResult("Page two text.", page_count=1),
                _FakeAnalyzeResult("Page three text.", page_count=1),
            ],
        )
        result = await ocr_module.extract_text(
            ["https://fake/p1.jpg", "https://fake/p2.jpg", "https://fake/p3.jpg"]
        )
        assert result.page_count == 3
        assert "Page one text." in result.ocr_text
        assert "Page two text." in result.ocr_text
        assert "Page three text." in result.ocr_text
        # Pages joined with the separator constant the service exposes.
        assert "\n\n---\n\n" in result.ocr_text

    @pytest.mark.asyncio
    async def test_extract_text_swallows_azure_errors(self, monkeypatch):
        _patch_analyze(monkeypatch, [RuntimeError("Azure DI 500"), RuntimeError("Azure DI 500")])
        result = await ocr_module.extract_text(["https://fake/p1.jpg", "https://fake/p2.jpg"])
        assert result.ocr_text == ""
        assert result.page_count == 2

    @pytest.mark.asyncio
    async def test_extract_text_no_urls(self, monkeypatch):
        # Should never reach Azure when input is empty.
        call_count = {"n": 0}

        async def should_not_call(*a, **kw):
            call_count["n"] += 1

        monkeypatch.setattr(ocr_module, "_analyze_single_url", should_not_call)
        result = await ocr_module.extract_text([])
        assert result.ocr_text == ""
        assert result.page_count == 1
        assert call_count["n"] == 0

    @pytest.mark.asyncio
    async def test_extract_text_picks_non_czech_language(self, monkeypatch):
        _patch_analyze(monkeypatch, [_FakeAnalyzeResult("Hallo Welt.", page_count=1, language="de")])
        result = await ocr_module.extract_text(["https://fake/p1.jpg"])
        assert result.document_language == "de"
