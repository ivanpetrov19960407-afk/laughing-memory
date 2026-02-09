.PHONY: ci docker-build docker-run install test

# Local CI: install deps and run tests (no interactive steps)
ci: install test

install:
	pip install -r requirements.txt

test:
	pytest

docker-build:
	docker build -t secretary-bot:latest .

# Run container; requires BOT_TOKEN and ALLOWED_USER_IDS via env or .env
# Example: make docker-run (with .env) or BOT_TOKEN=xxx ALLOWED_USER_IDS=1 make docker-run
docker-run:
	docker run --rm -it --env-file .env secretary-bot:latest
