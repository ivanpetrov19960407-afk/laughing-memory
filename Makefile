# Minimal targets for Infra-lite: venv, install, lint, fmt, test, run

.PHONY: venv install lint fmt test run

venv:
	python3 -m venv .venv
	@echo "Run: source .venv/bin/activate"

install:
	pip install -r requirements.txt
	@if [ -f .pre-commit-config.yaml ]; then pip install pre-commit 2>/dev/null; pre-commit install 2>/dev/null; fi

lint:
	@if command -v pre-commit >/dev/null 2>&1 && [ -f .pre-commit-config.yaml ]; then \
		pre-commit run --all-files; \
	else \
		python3 -m py_compile app/infra/config.py app/main.py 2>/dev/null || true; \
		echo "Install pre-commit and add .pre-commit-config.yaml for full lint"; \
	fi

fmt:
	@if command -v ruff >/dev/null 2>&1; then ruff format app tests; \
	elif command -v black >/dev/null 2>&1; then black app tests; \
	else echo "Install ruff or black for formatting"; fi

test:
	pytest -q

run:
	python bot.py
