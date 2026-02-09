# Infra-lite: venv, install, lint, test, fmt, run
# Compatible with requirements.txt and optional requirements-dev.txt

.PHONY: venv install lint test fmt run

venv:
	python3 -m venv .venv
	@echo "Activate with: source .venv/bin/activate"

install:
	pip install -r requirements.txt
	@if [ -f requirements-dev.txt ]; then pip install -r requirements-dev.txt; fi

lint:
	@command -v pre-commit >/dev/null 2>&1 && pre-commit run -a || (ruff check . 2>/dev/null || true)

test:
	pytest -q

fmt:
	@command -v pre-commit >/dev/null 2>&1 && pre-commit run -a || (ruff format . 2>/dev/null; ruff check --fix . 2>/dev/null; true)

run:
	python bot.py
