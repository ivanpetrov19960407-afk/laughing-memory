PYTHON ?= python3
VENV ?= .venv

.PHONY: venv install test ci docker-build docker-run

venv:
	$(PYTHON) -m venv $(VENV)

install: venv
	. $(VENV)/bin/activate && pip install --upgrade pip && pip install -r requirements.txt

test:
	$(PYTHON) -m pytest

ci: test

docker-build:
	docker build -t secretary-bot .

docker-run:
	docker run --rm --env-file .env --name secretary-bot secretary-bot

