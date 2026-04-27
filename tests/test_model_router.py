"""Tests for the analyzer model routing (escalation vs default)."""

from app.services.model_router import ESCALATION_CATEGORIES, pick_analyzer_model


class TestPickAnalyzerModel:
    def test_escalation_categories_match_skill(self):
        assert frozenset({"danove", "cizinecka", "soudni"}) == ESCALATION_CATEGORIES

    def test_escalation_categories_use_escalation_model(self):
        for cat in ESCALATION_CATEGORIES:
            model = pick_analyzer_model(cat)
            # Default config sets escalation = "gpt-4o"
            assert model == "gpt-4o"

    def test_routine_categories_use_default_model(self):
        for cat in ("ucty", "smlouvy", "zdravotni", "socialni", "urad_prace", "skolstvi", "ostatni"):
            assert pick_analyzer_model(cat) == "gpt-4o-mini"

    def test_unknown_category_falls_to_default(self):
        # Not in ESCALATION_CATEGORIES → default
        assert pick_analyzer_model("totally_unknown") == "gpt-4o-mini"
