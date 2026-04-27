"""Model routing — pick which OpenAI model to use for the analyzer call.

Skill: high-stakes categories go to the escalation model (currently `gpt-4o`,
target is `gpt-5.4-mini` once available). Everything else uses the default
mini-class analyzer.
"""

from app.config import get_settings

# Categories where mistakes are expensive (legal, financial, immigration).
# Skill: ESCALATION_CATEGORIES = {"danove", "cizinecka", "soudni"}.
ESCALATION_CATEGORIES: frozenset[str] = frozenset({"danove", "cizinecka", "soudni"})


def pick_analyzer_model(category: str) -> str:
    """Return the model id to use for the call-2 analyzer prompt."""
    settings = get_settings()
    if category in ESCALATION_CATEGORIES:
        return settings.openai_escalation_analyzer_model
    return settings.openai_default_analyzer_model
