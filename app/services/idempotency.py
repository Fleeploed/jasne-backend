"""In-memory idempotency for scan creation.

Mobile networks drop SSE/HTTP mid-flight; the client retries and the backend
runs a second full pipeline — two scans, two quota charges, two paid Azure OCR
calls. A client-supplied `Idempotency-Key` lets us collapse retries: a replay
of an in-flight key is rejected, and a replay of a completed key returns the
original scan instead of redoing the work.

Single-process in-memory (like rate_limit): one Render/Railway container, and
the claim/complete calls happen synchronously between awaits so there's no
intra-process race. Entries expire after a short window — long enough to cover
realistic retries, short enough to bound memory.
"""

from __future__ import annotations

import time
from typing import Literal, TypedDict

TTL_SECONDS = 600.0
_SWEEP_THRESHOLD = 5_000


class IdempotencyEntry(TypedDict):
    state: Literal["pending", "done"]
    scan_id: str | None
    ts: float


_store: dict[str, IdempotencyEntry] = {}


def _key(user_id: str, idem_key: str) -> str:
    return f"{user_id}:{idem_key}"


def _sweep(now: float) -> None:
    if len(_store) <= _SWEEP_THRESHOLD:
        return
    for k in [k for k, e in _store.items() if now - e["ts"] >= TTL_SECONDS]:
        del _store[k]


def claim(user_id: str, idem_key: str) -> IdempotencyEntry | None:
    """Claim the key for a new request.

    Returns None when the claim succeeds (caller owns the work). Returns the
    existing live entry when the key is already pending or recently completed
    (caller must short-circuit instead of redoing the pipeline).
    """
    now = time.monotonic()
    _sweep(now)
    k = _key(user_id, idem_key)
    entry = _store.get(k)
    if entry is not None and now - entry["ts"] < TTL_SECONDS:
        return entry
    _store[k] = {"state": "pending", "scan_id": None, "ts": now}
    return None


def complete(user_id: str, idem_key: str, scan_id: str) -> None:
    _store[_key(user_id, idem_key)] = {"state": "done", "scan_id": str(scan_id), "ts": time.monotonic()}


def release(user_id: str, idem_key: str) -> None:
    """Drop a pending claim so a genuine retry after a failure can proceed."""
    _store.pop(_key(user_id, idem_key), None)


def reset() -> None:
    """Clear all entries (tests only)."""
    _store.clear()
