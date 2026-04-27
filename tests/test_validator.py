"""Tests for the deadline validator — drops hallucinated/past dates."""

from datetime import date, timedelta

from app.models.schemas import DeadlineItem
from app.services.validator import (
    MAX_DEADLINE_HORIZON,
    coerce_deadline_type,
    filter_valid_deadlines,
)


def _d(days_offset: int, desc: str = "do something", type_: str = "action") -> DeadlineItem:
    return DeadlineItem(
        date=date.today() + timedelta(days=days_offset),
        description=desc,
        type=type_,  # type: ignore[arg-type]
    )


class TestFilterValidDeadlines:
    def test_keeps_future_deadlines(self):
        kept, rejected = filter_valid_deadlines([_d(7), _d(30), _d(365)])
        assert len(kept) == 3
        assert rejected == []

    def test_drops_past_deadlines(self):
        kept, rejected = filter_valid_deadlines([_d(-1), _d(-100), _d(7)])
        assert len(kept) == 1
        assert len(rejected) == 2
        assert all("past" in r for r in rejected)

    def test_drops_too_far_future(self):
        far = MAX_DEADLINE_HORIZON.days + 100
        kept, rejected = filter_valid_deadlines([_d(far)])
        assert kept == []
        assert "too far" in rejected[0]

    def test_drops_empty_description(self):
        kept, rejected = filter_valid_deadlines([_d(7, desc=""), _d(7, desc="   ")])
        assert kept == []
        assert len(rejected) == 2

    def test_keeps_today(self):
        # Today should still be valid (might be paying same-day)
        today_item = DeadlineItem(date=date.today(), description="now", type="payment")
        kept, _ = filter_valid_deadlines([today_item])
        assert len(kept) == 1


class TestCoerceDeadlineType:
    def test_known_types_unchanged(self):
        for t in ("payment", "action", "response", "expiration"):
            assert coerce_deadline_type(t) == t

    def test_unknown_falls_back_to_action(self):
        assert coerce_deadline_type("foobar") == "action"
        assert coerce_deadline_type("") == "action"
