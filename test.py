import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL = "https://ipc.gov.cz/informace-o-stavu-rizeni/"

CASE_NUMBER = "19391"
CASE_XX = ""  # для OAM-19391-4/ZM-2026 поставь "4"
CASE_TYPE = "ZM"
CASE_YEAR = "2026"


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def fill_input(page, name: str, value: str) -> None:
    page.locator(f'input[name="{name}"]').fill(value, timeout=10000)


def select_react_select_by_hidden_input(page, hidden_input_name: str, value: str) -> None:
    """
    Находит react-select по скрытому input[name="..."],
    кликает по его control и выбирает нужное значение.
    """
    hidden_input = page.locator(f'input[name="{hidden_input_name}"]')
    wrapper = hidden_input.locator("xpath=ancestor::div[contains(@class, 'select__wrapper')]")
    control = wrapper.locator(".react-select__control")

    control.click(timeout=10000)

    # В react-select меню часто рендерится отдельно в DOM.
    page.get_by_text(value, exact=True).click(timeout=10000)


def extract_result(page) -> str:
    body = normalize_text(page.locator("body").inner_text(timeout=10000))

    match = re.search(
        r"(Řízení či oznámení s číslem jednacím .*?)(?=Zadejte číslo jednací|$)",
        body,
        re.IGNORECASE,
    )

    if match:
        return normalize_text(match.group(1))

    raise RuntimeError("Не удалось найти блок результата на странице")


def main() -> None:
    case_id = (
        f"OAM-{CASE_NUMBER}-{CASE_XX}/{CASE_TYPE}-{CASE_YEAR}"
        if CASE_XX
        else f"OAM-{CASE_NUMBER}/{CASE_TYPE}-{CASE_YEAR}"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        page = browser.new_page(
            viewport={"width": 1400, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 "
                "Chrome/120.0.0.0 "
                "Safari/537.36"
            ),
        )

        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)

            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except PlaywrightTimeoutError:
                pass

            page.locator('input[name="proceedings.referenceNumber"]').wait_for(timeout=30000)

            fill_input(page, "proceedings.referenceNumber", CASE_NUMBER)

            if CASE_XX:
                fill_input(page, "proceedings.additionalSuffix", CASE_XX)

            select_react_select_by_hidden_input(page, "proceedings.category", CASE_TYPE)
            select_react_select_by_hidden_input(page, "proceedings.year", CASE_YEAR)

            page.locator('button[type="submit"]').click(timeout=10000)

            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except PlaywrightTimeoutError:
                pass

            page.wait_for_timeout(3000)

            result = extract_result(page)

            print("=" * 80)
            print(f"Дело: {case_id}")
            print("=" * 80)
            print(result)
            print("=" * 80)

        except Exception as e:
            page.screenshot(path="error_stav_rizeni.png", full_page=True)
            print("Ошибка:", e)
            print("Скриншот сохранён: error_stav_rizeni.png")
            raise

        finally:
            browser.close()


if __name__ == "__main__":
    main()