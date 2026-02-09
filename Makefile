# DevX: install, test, lint, format, run, precommit, clean
# Use: make install && make test && make precommit

.PHONY: install test lint format run precommit clean

install:
	pip install -r requirements.txt
	pip install -r requirements-dev.txt 2>/dev/null || true
	pre-commit install 2>/dev/null || true

test:
	pytest

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

run:
	python bot.py

precommit:
	pre-commit run -a

clean:
	rm -rf .pytest_cache
	rm -rf .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
