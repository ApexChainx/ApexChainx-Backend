.PHONY: bootstrap install dev install-dev lint typecheck test cov

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

format:
	ruff format .

typecheck:
	mypy app/

test:
	pytest

cov:
	pytest --cov-branch --cov-fail-under=80 --cov=app/services/ --cov=app/utils/ --cov=app/db/
