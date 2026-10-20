from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any


@dataclass(frozen=True)
class CheckResult:
    status: str
    raw_data: dict[str, Any] = field(default_factory=dict)


class BaseTracker(ABC):
    """Interface every background tracker type must implement."""

    def normalize_identifier(self, identifier: str) -> str:
        return " ".join(identifier.strip().split())

    def initial_status_from_metadata(self, metadata: dict[str, Any]) -> str | None:
        """Return a trusted enough status hint for create-time UX, if this tracker supports one."""
        return None

    @abstractmethod
    async def check(self, identifier: str, metadata: dict[str, Any]) -> CheckResult:
        raise NotImplementedError

    @abstractmethod
    def validate_identifier(self, identifier: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_next_check_interval(self, tracking: dict[str, Any]) -> timedelta:
        raise NotImplementedError

    @abstractmethod
    def get_push_message(
        self,
        language: str,
        status: str,
        identifier: str,
        metadata: dict[str, Any],
    ) -> tuple[str, str]:
        raise NotImplementedError

    @abstractmethod
    def is_terminal_status(self, status: str) -> bool:
        raise NotImplementedError
