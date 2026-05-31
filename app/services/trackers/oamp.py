from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from app.prompts.push_messages import tracking_push_copy
from app.services.oamp_client import OampTrackedStatus, check_status
from app.services.trackers.base import BaseTracker, CheckResult

OAMP_IDENTIFIER_RE = re.compile(r"^OAM-\d+/[A-Z]+-\d{4}$")
NOT_FOUND_STOP_THRESHOLD = 3
ERROR_STOP_THRESHOLD = 5


class OampTracker(BaseTracker):
    def normalize_identifier(self, identifier: str) -> str:
        cleaned = re.sub(r"\s+", "", identifier.strip().upper())
        return re.sub(r"^0AM", "OAM", cleaned)

    def validate_identifier(self, identifier: str) -> bool:
        return bool(OAMP_IDENTIFIER_RE.fullmatch(self.normalize_identifier(identifier)))

    async def check(self, identifier: str, metadata: dict[str, Any]) -> CheckResult:
        status = await check_status(self.normalize_identifier(identifier))
        raw_data = self._next_retry_metadata(status, metadata)
        if status == "not_found" and raw_data["not_found_count"] >= NOT_FOUND_STOP_THRESHOLD:
            return CheckResult(status="not_found_stopped", raw_data=raw_data)
        if status == "error" and raw_data["error_count"] >= ERROR_STOP_THRESHOLD:
            return CheckResult(status="error_stopped", raw_data=raw_data)
        return CheckResult(status=status, raw_data=raw_data)

    def get_next_check_interval(self, tracking: dict[str, Any]) -> timedelta:
        status = str(tracking.get("current_status") or "")
        if status in {"error", "error_stopped"}:
            return timedelta(hours=1)
        if status in {"not_found", "not_found_stopped"}:
            return timedelta(days=1)
        if self.is_terminal_status(status):
            return timedelta(0)

        created_at = _parse_datetime(tracking.get("created_at"))
        age = datetime.now(UTC) - created_at
        if age < timedelta(days=30):
            return timedelta(days=7)
        if age <= timedelta(days=90):
            return timedelta(days=3)
        return timedelta(days=1)

    def get_push_message(
        self,
        language: str,
        status: str,
        identifier: str,
        metadata: dict[str, Any],
    ) -> tuple[str, str]:
        return tracking_push_copy("oamp_status", status, language, identifier, metadata)

    def is_terminal_status(self, status: str) -> bool:
        return status in {"schvaleno", "zamitnuto", "not_found_stopped", "error_stopped"}

    def _next_retry_metadata(self, status: OampTrackedStatus, metadata: dict[str, Any]) -> dict[str, Any]:
        not_found_count = int(metadata.get("not_found_count") or 0)
        error_count = int(metadata.get("error_count") or 0)
        if status == "not_found":
            not_found_count += 1
            error_count = 0
        elif status == "error":
            error_count += 1
            not_found_count = 0
        else:
            not_found_count = 0
            error_count = 0
        return {
            "not_found_count": not_found_count,
            "error_count": error_count,
        }


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        return datetime.now(UTC)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
