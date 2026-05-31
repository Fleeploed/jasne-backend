from __future__ import annotations

from app.models.schemas import UserLanguage

DEFAULT_LANGUAGE: UserLanguage = "cs"

PUSH_TITLES: dict[UserLanguage, str] = {
    "cs": "Aktualizace žádosti",
    "en": "Application update",
    "uk": "Оновлення заяви",
    "ru": "Обновление заявления",
}

PUSH_MESSAGES: dict[str, dict[UserLanguage, str]] = {
    "schvaleno": {
        "cs": "✅ Rozhodnutí k {cislo_jednaci} je schválené. Můžete se objednat na převzetí.",
        "en": "✅ Application {cislo_jednaci} was approved. You can book pickup.",
        "uk": "✅ Рішення щодо {cislo_jednaci} схвалено. Можна записуватися на převzetí.",
        "ru": "✅ Решение по {cislo_jednaci} одобрено! Можно записываться на převzetí.",
    },
    "zamitnuto": {
        "cs": "📬 U žádosti {cislo_jednaci} bylo vydáno rozhodnutí. Otevřete aplikaci pro detaily.",
        "en": "📬 A decision was issued for application {cislo_jednaci}. Open the app for details.",
        "uk": "📬 За заявою {cislo_jednaci} винесено рішення. Відкрийте застосунок для деталей.",
        "ru": "📬 По žádosti {cislo_jednaci} вынесено решение. Откройте приложение для деталей.",
    },
    "not_found_stopped": {
        "cs": "Nepodařilo se ověřit stav {cislo_jednaci}. Zkontrolujte číslo ručně.",
        "en": "We could not verify {cislo_jednaci}. Please check the number manually.",
        "uk": "Не вдалося перевірити статус {cislo_jednaci}. Перевірте номер вручну.",
        "ru": "Не удаётся проверить статус {cislo_jednaci}. Проверьте номер вручную.",
    },
    "error_stopped": {
        "cs": "Stav {cislo_jednaci} se opakovaně nepodařilo ověřit. Zkuste ruční kontrolu.",
        "en": "Status checks for {cislo_jednaci} keep failing. Please try a manual check.",
        "uk": "Статус {cislo_jednaci} не вдається перевірити. Спробуйте ручну перевірку.",
        "ru": "Не удаётся проверить статус {cislo_jednaci}. Попробуйте проверить вручную.",
    },
}


def status_push_copy(new_status: str, language: str, cislo_jednaci: str) -> tuple[str, str]:
    lang: UserLanguage = language if language in {"cs", "en", "uk", "ru"} else DEFAULT_LANGUAGE
    messages = PUSH_MESSAGES.get(new_status) or PUSH_MESSAGES["error_stopped"]
    template = messages.get(lang) or messages[DEFAULT_LANGUAGE]
    title = PUSH_TITLES.get(lang) or PUSH_TITLES[DEFAULT_LANGUAGE]
    return title, template.format(cislo_jednaci=_cislo_jednaci_safe(cislo_jednaci))


def _cislo_jednaci_safe(value: str) -> str:
    return " ".join(value.split())[:80]
