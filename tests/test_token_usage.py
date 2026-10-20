"""Tests for OpenAI token-usage extraction + structured logging."""

import logging
from types import SimpleNamespace

from app.services.token_usage import extract_usage, log_usage


class TestExtractUsage:
    def test_reads_openai_usage_object(self):
        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=120, completion_tokens=80, total_tokens=200)
        )
        assert extract_usage(response) == {
            "prompt_tokens": 120,
            "completion_tokens": 80,
            "total_tokens": 200,
        }

    def test_derives_total_when_missing(self):
        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=None)
        )
        assert extract_usage(response)["total_tokens"] == 15

    def test_none_when_no_usage(self):
        assert extract_usage(SimpleNamespace(usage=None)) is None
        assert extract_usage(SimpleNamespace()) is None


class TestLogUsage:
    def test_emits_structured_line(self, caplog):
        with caplog.at_level(logging.INFO, logger="jasne.token_usage"):
            log_usage(
                "analyze",
                "gpt-4o",
                {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                category="danove",
            )
        assert "llm_usage" in caplog.text
        assert "phase=analyze" in caplog.text
        assert "category=danove" in caplog.text

    def test_noop_without_usage(self, caplog):
        with caplog.at_level(logging.INFO, logger="jasne.token_usage"):
            log_usage("analyze", "gpt-4o", None)
        assert caplog.text == ""
