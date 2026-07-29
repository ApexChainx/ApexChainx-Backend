.PHONY: bootstrap install dev install-dev lint typecheck test

bootstrap:
	pip install pip-tools
	pip-compile pyproject.toml --generate-hashes -o requirements.lock
	pip install -r requirements.lock
	pip install -e .

install:
	pip install -r requirements.lock

dev: install
	pip install -e ".[dev]"

lint:
	ruff check .

typecheck:
	mypy app/

test:
	pytest
