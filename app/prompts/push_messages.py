from __future__ import annotations

from typing import Any

from app.models.schemas import UserLanguage

DEFAULT_LANGUAGE: UserLanguage = "cs"

TRACKING_PUSH_TITLES: dict[str, dict[UserLanguage, str]] = {
    "oamp_status": {
        "cs": "Aktualizace žádosti",
        "en": "Application update",
        "uk": "Оновлення заяви",
        "ru": "Обновление заявления",
    },
}

TRACKING_PUSH_MESSAGES: dict[str, dict[str, dict[UserLanguage, str]]] = {
    "oamp_status": {
        "probiha": {
            "cs": "Žádost {identifier} se stále zpracovává.",
            "en": "Application {identifier} is still being processed.",
            "uk": "Заява {identifier} ще обробляється.",
            "ru": "Žádost {identifier} всё ещё в обработке.",
        },
        "schvaleno": {
            "cs": "✅ Rozhodnutí k {identifier} je schválené. Můžete se objednat na převzetí.",
            "en": "✅ Application {identifier} was approved. You can book pickup.",
            "uk": "✅ Рішення щодо {identifier} схвалено. Можна записуватися на převzetí.",
            "ru": "✅ Решение по {identifier} одобрено! Можно записываться на převzetí.",
        },
        "zamitnuto": {
            "cs": "📬 U žádosti {identifier} bylo vydáno rozhodnutí. Otevřete aplikaci pro detaily.",
            "en": "📬 A decision was issued for application {identifier}. Open the app for details.",
            "uk": "📬 За žádostí {identifier} винесено рішення. Відкрийте застосунок для деталей.",
            "ru": "📬 По žádosti {identifier} вынесено решение. Откройте приложение для деталей.",
        },
        "not_found": {
            "cs": "Žádost {identifier} se teď nepodařilo najít. Zkontrolujeme ji znovu.",
            "en": "Application {identifier} could not be found right now. We will check again.",
            "uk": "Заяву {identifier} зараз не знайдено. Ми перевіримо ще раз.",
            "ru": "Žádost {identifier} сейчас не найдена. Мы проверим ещё раз.",
        },
        "error": {
            "cs": "Stav {identifier} se teď nepodařilo ověřit. Zkusíme to znovu.",
            "en": "We could not verify {identifier} right now. We will try again.",
            "uk": "Не вдалося перевірити статус {identifier}. Ми спробуємо ще раз.",
            "ru": "Не удалось проверить статус {identifier}. Мы попробуем ещё раз.",
        },
        "not_found_stopped": {
            "cs": "Nepodařilo se ověřit stav {identifier}. Zkontrolujte číslo ručně.",
            "en": "We could not verify {identifier}. Please check the number manually.",
            "uk": "Не вдалося перевірити статус {identifier}. Перевірте номер вручну.",
            "ru": "Не удаётся проверить статус {identifier}. Проверьте номер вручную.",
        },
        "error_stopped": {
            "cs": "Stav {identifier} se opakovaně nepodařilo ověřit. Zkuste ruční kontrolu.",
            "en": "Status checks for {identifier} keep failing. Please try a manual check.",
            "uk": "Статус {identifier} не вдається перевірити. Спробуйте ручну перевірку.",
            "ru": "Не удаётся проверить статус {identifier}. Попробуйте проверить вручную.",
        },
    },
}


def tracking_push_copy(
    tracking_type: str,
    status: str,
    language: str,
    identifier: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[str, str]:
    lang: UserLanguage = language if language in {"cs", "en", "uk", "ru"} else DEFAULT_LANGUAGE
    title_by_language = TRACKING_PUSH_TITLES.get(tracking_type) or TRACKING_PUSH_TITLES["oamp_status"]
    messages_by_status = TRACKING_PUSH_MESSAGES.get(tracking_type) or TRACKING_PUSH_MESSAGES["oamp_status"]
    messages = messages_by_status.get(status) or messages_by_status["error"]
    template = messages.get(lang) or messages[DEFAULT_LANGUAGE]
    title = title_by_language.get(lang) or title_by_language[DEFAULT_LANGUAGE]
    return title, template.format(identifier=_identifier_safe(identifier), **(metadata or {}))


def _identifier_safe(value: str) -> str:
    return " ".join(value.split())[:120]
