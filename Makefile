.PHONY: install install-dev test test-fast test-cov test-watch lint run clean

VENV := venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest

install:
	$(PIP) install -r requirements.txt

install-dev: install
	$(PIP) install -r requirements-dev.txt

# Полный прогон с подробным выводом
test:
	$(PYTEST) -v

# Быстрый прогон — только падающие с прошлого раза, параллельно
test-fast:
	$(PYTEST) -x --ff -q

# С покрытием (HTML-отчёт в htmlcov/index.html)
test-cov:
	$(PYTEST) --cov=app --cov-report=term-missing --cov-report=html

# Авто-перезапуск при сохранении файла (pytest-watch)
test-watch:
	$(VENV)/bin/ptw -- -q

# Запуск сервера
run:
	$(VENV)/bin/uvicorn app.main:app --reload --port 8000

# Линтер + формат (если поставишь ruff)
lint:
	$(VENV)/bin/ruff check app tests
	$(VENV)/bin/ruff format --check app tests

clean:
	rm -rf .pytest_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
