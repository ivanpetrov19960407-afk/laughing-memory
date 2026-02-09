# DevX: install, test, lint, format, run, precommit, clean
# Use: make venv && make install && make test && make precommit
# Cross-platform: uses python -m pip, python -m pytest for Windows/Ubuntu compatibility

.PHONY: venv install test test-fast lint format run precommit clean

venv:
	@if [ ! -d .venv ]; then \
		python -m venv .venv; \
		echo "Created .venv"; \
	else \
		echo ".venv already exists"; \
	fi

install:
	python -m pip install -r requirements.txt
	python -m pip install -r requirements-dev.txt 2>/dev/null || true
	pre-commit install 2>/dev/null || true

test:
	python -m pytest -q

test-fast:
	python -m pytest -q -k "not integration"

lint:
	python -m ruff check .
	python -m ruff format --check .

format:
	python -m ruff check --fix .
	python -m ruff format .

run:
	python bot.py

precommit:
	pre-commit run -a

clean:
	rm -rf .pytest_cache 2>/dev/null || true
	rm -rf .ruff_cache 2>/dev/null || true
	rm -rf __pycache__ 2>/dev/null || true
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
