"""Pure schema validation tests — no mocks needed."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.schemas import (
    AnalysisResult,
    ClassifierResult,
    DeadlineCreate,
    DeadlineItem,
    DeadlineUpdate,
    HealthResponse,
    ProfileUpdate,
    ScanListItem,
    ScanRequest,
)


class TestClassifierResult:
    def test_valid(self):
        r = ClassifierResult(
            ocr_text="hello",
            category="danove",
            doc_type="platebni_vymer",
            sender="FÚ",
            document_language="cs",
            confidence=0.95,
        )
        assert r.category == "danove"
        assert r.confidence == 0.95
        assert r.error is None

    def test_invalid_category_rejected(self):
        with pytest.raises(ValidationError):
            ClassifierResult(
                ocr_text="x",
                category="unknown",  # not in Literal
                doc_type="x",
                document_language="cs",
                confidence=0.5,
            )

    def test_confidence_range(self):
        with pytest.raises(ValidationError):
            ClassifierResult(
                ocr_text="x",
                category="danove",
                doc_type="x",
                document_language="cs",
                confidence=1.5,
            )
        with pytest.raises(ValidationError):
            ClassifierResult(
                ocr_text="x",
                category="danove",
                doc_type="x",
                document_language="cs",
                confidence=-0.1,
            )

    def test_defaults(self):
        r = ClassifierResult(
            ocr_text="x",
            category="ostatni",
            doc_type="y",
            document_language="cs",
            confidence=0.3,
        )
        assert r.sender is None
        assert r.error is None


class TestAnalysisResult:
    def test_valid_minimal(self):
        r = AnalysisResult(
            document_type="x",
            document_type_cz="X",
            summary="summary",
            confidence=0.9,
        )
        assert r.deadlines == []
        assert r.actions_required == []
        assert r.documents_needed == []

    def test_with_deadlines(self):
        r = AnalysisResult(
            document_type="x",
            document_type_cz="X",
            summary="s",
            deadlines=[DeadlineItem(date=date(2026, 5, 1), description="pay", type="payment")],
            confidence=0.8,
        )
        assert len(r.deadlines) == 1
        assert r.deadlines[0].type == "payment"

    def test_deadline_type_validation(self):
        with pytest.raises(ValidationError):
            DeadlineItem(date=date(2026, 5, 1), description="x", type="bogus")


class TestScanRequest:
    def test_minimal(self):
        r = ScanRequest(image_path="user-id/doc.jpg")
        assert r.language is None

    def test_with_language_override(self):
        r = ScanRequest(image_path="user-id/doc.jpg", language="ru")
        assert r.language == "ru"

    def test_empty_path_rejected(self):
        with pytest.raises(ValidationError):
            ScanRequest(image_path="")

    def test_invalid_language(self):
        with pytest.raises(ValidationError):
            ScanRequest(image_path="x/y.jpg", language="de")


class TestScanListItem:
    def test_from_row_full(self):
        row = {
            "id": uuid4(),
            "category": "danove",
            "doc_type": "platebni_vymer",
            "result": {"summary": "pay it", "confidence": 0.9},
            "created_at": datetime.now(UTC),
        }
        item = ScanListItem.from_row(row)
        assert item.summary == "pay it"
        assert item.confidence == 0.9

    def test_from_row_no_result(self):
        row = {
            "id": uuid4(),
            "category": "ostatni",
            "doc_type": None,
            "result": None,
            "created_at": datetime.now(UTC),
        }
        item = ScanListItem.from_row(row)
        assert item.summary is None
        assert item.confidence is None


class TestDeadlineCreate:
    def test_defaults_reminder_days(self):
        d = DeadlineCreate(title="pay", due_date=date(2026, 5, 1))
        assert d.reminder_days == [30, 14, 7, 1]
        assert d.deadline_type == "action"

    def test_reminder_days_sorted_dedup(self):
        d = DeadlineCreate(
            title="pay",
            due_date=date(2026, 5, 1),
            reminder_days=[1, 7, 7, 30, 14],
        )
        assert d.reminder_days == [30, 14, 7, 1]

    def test_negative_reminder_days_rejected(self):
        with pytest.raises(ValidationError):
            DeadlineCreate(title="x", due_date=date(2026, 5, 1), reminder_days=[-1])

    def test_empty_title_rejected(self):
        with pytest.raises(ValidationError):
            DeadlineCreate(title="", due_date=date(2026, 5, 1))

    def test_long_title_rejected(self):
        with pytest.raises(ValidationError):
            DeadlineCreate(title="x" * 201, due_date=date(2026, 5, 1))


class TestDeadlineUpdate:
    def test_partial_updates_allowed(self):
        u = DeadlineUpdate(status="completed")
        assert u.status == "completed"
        assert u.title is None

    def test_all_optional(self):
        u = DeadlineUpdate()
        assert u.model_dump(exclude_none=True) == {}


class TestProfileUpdate:
    def test_partial(self):
        u = ProfileUpdate(language="ru")
        assert u.model_dump(exclude_none=True) == {"language": "ru"}

    def test_all_optional(self):
        u = ProfileUpdate()
        assert u.model_dump(exclude_none=True) == {}

    def test_display_name_max_length(self):
        with pytest.raises(ValidationError):
            ProfileUpdate(display_name="x" * 121)


class TestHealthResponse:
    def test_default(self):
        r = HealthResponse(environment="test")
        assert r.status == "ok"
        assert r.environment == "test"
        assert r.version == "0.1.0"
