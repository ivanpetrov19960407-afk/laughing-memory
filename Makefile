# Local development and CI
.PHONY: install test ci docker-build docker-run

# Install dependencies (local venv)
install:
	pip install -r requirements.txt

# Run tests (same as CI locally)
test:
	pytest -v

# Alias for local CI run
ci: test

# Docker: build image (optional: make docker-build PYTHON_VERSION=3.11)
docker-build:
	docker build $$(test -z "$$PYTHON_VERSION" || echo "--build-arg PYTHON_VERSION=$$PYTHON_VERSION") -t telegram-bot .

# Docker: run container (set BOT_TOKEN; optional ALLOWED_USER_IDS, etc.)
docker-run:
	docker run --rm -e BOT_TOKEN=$${BOT_TOKEN} -e ALLOWED_USER_IDS=$${ALLOWED_USER_IDS:-} telegram-bot
