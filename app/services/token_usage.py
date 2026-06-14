"""Structured OpenAI token-usage logging.

Economics ($/scan, escalation cost) was estimated, not measured. Emitting one
structured line per LLM call with prompt/completion tokens + model + category
means a month of logs yields real per-category, per-model cost — the input a
pricing/escalation decision actually needs.

Kept to logging (no DB column) on purpose: zero migration, zero hot-path
signature churn. Pipe the `llm_usage` lines to any log drain to aggregate.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("jasne.token_usage")


def extract_usage(response: Any) -> dict[str, int] | None:
    """Pull prompt/completion/total tokens out of an OpenAI response.

    Returns None when usage is absent (e.g. a streamed response created without
    stream_options={'include_usage': True}, or a mock in tests).
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    total = getattr(usage, "total_tokens", None)
    if prompt is None and completion is None and total is None:
        return None
    return {
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
        "total_tokens": int(total or (prompt or 0) + (completion or 0)),
    }


def log_usage(phase: str, model: str, usage: dict[str, int] | None, **extra: Any) -> None:
    """Emit one structured `llm_usage` line. No-op when usage is unavailable."""
    if not usage:
        return
    extra_str = " ".join(f"{k}={v}" for k, v in extra.items() if v is not None)
    logger.info(
        "llm_usage phase=%s model=%s prompt=%d completion=%d total=%d %s",
        phase,
        model,
        usage["prompt_tokens"],
        usage["completion_tokens"],
        usage["total_tokens"],
        extra_str,
    )
