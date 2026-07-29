.PHONY: bootstrap install dev install-dev lint typecheck test cov
.PHONY: bootstrap install dev install-dev lint typecheck test help format test-cov migrate clean

help: ## Show all targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

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

test: ## Run tests
	pytest

cov:
	pytest --cov-branch --cov-fail-under=80 --cov=app/services/ --cov=app/utils/ --cov=app/db/
test-cov: ## Run tests with coverage
	coverage run -m pytest && coverage report -m

format: ## Format code with ruff
	ruff format .

migrate: ## Run alembic migrations
	alembic upgrade head

clean: ## Remove build artifacts
	rm -rf __pycache__ .pytest_cache .mypy_cache *.egg-info

CI: lint format typecheck test ## Full CI pipeline
