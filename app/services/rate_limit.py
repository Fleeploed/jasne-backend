"""Per-user in-memory rate limiting for expensive endpoints.

Quota (usage_limits) only charges *successful* scans, so failed pipelines —
e.g. 10-page PDFs of garbage that die at the OCR quality gate — would
otherwise generate unlimited paid Azure calls. This limiter counts attempts
regardless of outcome.

Single-process by design: the backend runs as one container (Railway/Render),
so a process-local sliding window is sufficient. Counters reset on
deploy/restart — acceptable for cost-abuse protection, not for billing.
"""

from __future__ import annotations

import time
from collections import deque

_BUCKETS: dict[str, deque[float]] = {}

# Drop empty buckets once the dict grows past this, so memory stays bounded
# even with many one-off users.
_SWEEP_THRESHOLD = 10_000


def check_rate_limit(key: str, limit: int, window_seconds: float) -> bool:
    """Record an attempt for `key`; return False when the attempt is over limit.

    Sliding window: an attempt counts against the limit for `window_seconds`.
    Rejected attempts are not recorded, so a throttled client recovers as soon
    as old attempts age out instead of being locked out forever.
    """
    now = time.monotonic()
    bucket = _BUCKETS.setdefault(key, deque())
    cutoff = now - window_seconds
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)

    if len(_BUCKETS) > _SWEEP_THRESHOLD:
        for stale_key in [k for k, b in _BUCKETS.items() if not b]:
            del _BUCKETS[stale_key]
    return True


def reset() -> None:
    """Clear all counters (tests only)."""
    _BUCKETS.clear()
