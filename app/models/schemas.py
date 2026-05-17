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
UserPlan = Literal["free", "pro"]
MessageRole = Literal["user", "assistant"]
MessageType = Literal["text", "image", "result_card"]

DEFAULT_REMINDER_DAYS: list[int] = [30, 14, 7, 1]

# Hard cap on pages per scan — also enforced in the DB CHECK constraint.
# Plan-specific limits (3 free / 10 pro) layer on top of this in services/limits.
MAX_PAGES_HARD_LIMIT: int = 10


class _Base(BaseModel):
    # `protected_namespaces=()` disables Pydantic v2's `model_` collision check —
    # we have legitimate domain field names like ScanResponse.model_used.
    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
        protected_namespaces=(),
    )


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class ProfileResponse(_Base):
    id: UUID
    display_name: str | None = None
    language: UserLanguage
    has_datova_schranka: bool
    plan: UserPlan = "free"
    # Subscription details mirrored from RevenueCat. Populated by the
    # webhook handler; `None` for users who never subscribed.
    subscription_product_id: str | None = None
    subscription_expires_at: datetime | None = None
    subscription_will_renew: bool = False
    created_at: datetime
    updated_at: datetime


class ProfileUpdate(_Base):
    display_name: str | None = Field(default=None, max_length=120)
    language: UserLanguage | None = None
    has_datova_schranka: bool | None = None
    # `plan` is intentionally NOT updatable via this endpoint — it's owned by
    # the RevenueCat webhook (server-to-server) once payments are wired up.


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
    page_count: int = Field(default=1, ge=1, le=MAX_PAGES_HARD_LIMIT)
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
    office_hours: str | None = None
    documents_needed: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    tips: str | None = None


# ---------------------------------------------------------------------------
# Scan — multi-page
# ---------------------------------------------------------------------------


class ScanRequest(_Base):
    image_paths: list[str] = Field(
        min_length=1,
        max_length=MAX_PAGES_HARD_LIMIT,
        description="Storage paths in the documents bucket. Images are sent as pages; PDFs are rendered to pages.",
    )
    language: UserLanguage | None = Field(
        default=None,
        description="Override the language for this scan. Falls back to profile.language.",
    )

    @field_validator("image_paths")
    @classmethod
    def _no_blank_paths(cls, v: list[str]) -> list[str]:
        cleaned = [p.strip() for p in v if p.strip()]
        if not cleaned:
            raise ValueError("image_paths must contain at least one non-empty path")
        return cleaned


class ScanResponse(_Base):
    id: UUID
    user_id: UUID
    image_paths: list[str]
    page_count: int
    ocr_text: str | None = None
    category: DocumentCategory | None = None
    doc_type: str | None = None
    category_confidence: float | None = None
    result: AnalysisResult | None = None
    language: UserLanguage
    model_used: str | None = None
    created_at: datetime


class ScanImageUrlsResponse(_Base):
    urls: list[str]


class ScanListItem(_Base):
    id: UUID
    category: DocumentCategory | None = None
    doc_type: str | None = None
    document_type_cz: str | None = None
    summary: str | None = None
    confidence: float | None = None
    page_count: int = 1
    created_at: datetime
    last_message_at: datetime | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ScanListItem:
        result = row.get("result") or {}
        return cls(
            id=row["id"],
            category=row.get("category"),
            doc_type=row.get("doc_type"),
            document_type_cz=result.get("document_type_cz"),
            summary=result.get("summary"),
            confidence=result.get("confidence"),
            page_count=row.get("page_count") or len(row.get("image_paths") or []) or 1,
            created_at=row["created_at"],
            last_message_at=row.get("last_message_at"),
        )


# ---------------------------------------------------------------------------
# Chat — questions about an existing scan (Pro feature)
# ---------------------------------------------------------------------------


class ScanMessage(_Base):
    id: UUID
    scan_id: UUID
    role: MessageRole
    content: str
    message_type: MessageType
    created_at: datetime


class ChatAskRequest(_Base):
    question: str = Field(min_length=1, max_length=1000)


class ChatAskResponse(_Base):
    user_message: ScanMessage
    assistant_message: ScanMessage
    questions_used: int  # how many questions consumed for this scan in current month
    questions_remaining: int


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
    scan_id: UUID | None = None
    due_date: date | None = None
    reminder_days: list[int] | None = None
    deadline_type: DeadlineType | None = None
    status: DeadlineStatus | None = None

    @field_validator("reminder_days")
    @classmethod
    def reminder_days_positive(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        if any(d < 0 for d in v):
            raise ValueError("reminder_days must be non-negative")
        return sorted(set(v), reverse=True)


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
    auto_created: bool = False
    created_at: datetime


# ---------------------------------------------------------------------------
# Limits — current usage vs plan caps (used by mobile to gate UI)
# ---------------------------------------------------------------------------


class LimitsResponse(_Base):
    plan: UserPlan
    month: str  # YYYY-MM, the period these counters cover

    scans_used: int
    scans_max: int
    scans_remaining: int

    max_pages_per_scan: int

    # questions consumed for one specific scan (only populated when ?scan_id= is
    # passed). null when called without a scan filter.
    questions_used_for_scan: int | None = None
    max_questions_per_scan: int


# ---------------------------------------------------------------------------
# API wrappers / system
# ---------------------------------------------------------------------------


class ErrorResponse(_Base):
    detail: str


class HealthResponse(_Base):
    status: Literal["ok"] = "ok"
    environment: str = "development"
    version: str = "0.1.0"


class DepStatus(_Base):
    """Status of a single external dependency for the deep health probe."""

    status: Literal["ok", "error"]
    latency_ms: int | None = None
    error: str | None = None


class DeepHealthResponse(_Base):
    """Deep readiness probe — overall status reflects all dependencies."""

    status: Literal["ok", "degraded"]
    environment: str
    version: str = "0.1.0"
    deps: dict[str, DepStatus] = Field(default_factory=dict)
