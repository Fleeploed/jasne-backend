"""Language-specific analyzer prompt modules."""

from app.prompts.language_cs import CS_LANGUAGE_MODULE
from app.prompts.language_en import EN_LANGUAGE_MODULE
from app.prompts.language_ru import RU_LANGUAGE_MODULE
from app.prompts.language_uk import UK_LANGUAGE_MODULE

LANGUAGE_MODULES: dict[str, str] = {
    "cs": CS_LANGUAGE_MODULE,
    "en": EN_LANGUAGE_MODULE,
    "ru": RU_LANGUAGE_MODULE,
    "uk": UK_LANGUAGE_MODULE,
}
