from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Enums / literals
# ---------------------------------------------------------------------------

DocumentCategory = Literal[
    "danove",
    "socialni",
    "zdravotni",
    "cizinecka",
    "smlouvy",
    "ucty",
    "soudni",
    "urad_prace",
    "skolstvi",
    "ostatni",
]

DeadlineType = Literal["payment", "action", "response", "expiration"]
DeadlineStatus = Literal["active", "completed", "missed"]
UserLanguage = Literal["cs", "en", "uk", "ru"]

DEFAULT_REMINDER_DAYS: list[int] = [30, 14, 7, 1]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class ProfileResponse(_Base):
    id: UUID
    display_name: str | None = None
    language: UserLanguage
    has_datova_schranka: bool
    created_at: datetime
    updated_at: datetime


class ProfileUpdate(_Base):
    display_name: str | None = Field(default=None, max_length=120)
    language: UserLanguage | None = None
    has_datova_schranka: bool | None = None


# ---------------------------------------------------------------------------
# Classifier (LLM call 1 output)
# ---------------------------------------------------------------------------


class ClassifierResult(_Base):
    ocr_text: str
    category: DocumentCategory
    doc_type: str
    sender: str | None = None
    document_language: str = "cs"
    confidence: float = Field(ge=0.0, le=1.0)
    error: str | None = None


# ---------------------------------------------------------------------------
# Analysis / LLM call 2 output
# ---------------------------------------------------------------------------


class DeadlineItem(_Base):
    date: date
    description: str
    type: DeadlineType


class AnalysisResult(_Base):
    document_type: str
    document_type_cz: str
    document_type_localized: str | None = None
    sender: str | None = None
    summary: str
    translation: str | None = None
    deadlines: list[DeadlineItem] = Field(default_factory=list)
    actions_required: list[str] = Field(default_factory=list)
    penalty: str | None = None
    where_to_go: str | None = None
    documents_needed: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    tips: str | None = None


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


class ScanRequest(_Base):
    image_path: str = Field(min_length=1, description="Path in Supabase Storage, e.g. {user_id}/file.jpg")
    language: UserLanguage | None = Field(
        default=None,
        description="Override the language for this scan. Falls back to profile.language.",
    )


class ScanResponse(_Base):
    id: UUID
    user_id: UUID
    image_path: str
    ocr_text: str | None = None
    category: DocumentCategory | None = None
    doc_type: str | None = None
    category_confidence: float | None = None
    result: AnalysisResult | None = None
    language: UserLanguage
    model_used: str | None = None
    created_at: datetime


class ScanListItem(_Base):
    id: UUID
    category: DocumentCategory | None = None
    doc_type: str | None = None
    summary: str | None = None
    confidence: float | None = None
    created_at: datetime

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ScanListItem:
        result = row.get("result") or {}
        return cls(
            id=row["id"],
            category=row.get("category"),
            doc_type=row.get("doc_type"),
            summary=result.get("summary"),
            confidence=result.get("confidence"),
            created_at=row["created_at"],
        )


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------


class DeadlineCreate(_Base):
    scan_id: UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    due_date: date
    reminder_days: list[int] = Field(default_factory=lambda: list(DEFAULT_REMINDER_DAYS))
    deadline_type: DeadlineType = "action"

    @field_validator("reminder_days")
    @classmethod
    def reminder_days_positive(cls, v: list[int]) -> list[int]:
        if any(d < 0 for d in v):
            raise ValueError("reminder_days must be non-negative")
        return sorted(set(v), reverse=True)


class DeadlineUpdate(_Base):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    due_date: date | None = None
    reminder_days: list[int] | None = None
    deadline_type: DeadlineType | None = None
    status: DeadlineStatus | None = None


class DeadlineResponse(_Base):
    id: UUID
    user_id: UUID
    scan_id: UUID | None = None
    title: str
    description: str | None = None
    due_date: date
    reminder_days: list[int]
    deadline_type: DeadlineType
    status: DeadlineStatus
    created_at: datetime


# ---------------------------------------------------------------------------
# API wrappers
# ---------------------------------------------------------------------------


class ErrorResponse(_Base):
    detail: str


class HealthResponse(_Base):
    status: Literal["ok"] = "ok"
    environment: str = "development"
    version: str = "0.1.0"
