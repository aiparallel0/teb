# ─── teb — Development Makefile ───────────────────────────────────────────────
# Usage: make <target>
# Run `make help` to see all available targets.

.PHONY: help install dev test test-cov lint format typecheck check clean run

PYTHON ?= python
PIP ?= pip

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install production dependencies
	$(PIP) install -r requirements.txt

dev: ## Install development dependencies
	$(PIP) install -r requirements.txt
	$(PIP) install -e ".[dev]"

test: ## Run all tests
	$(PYTHON) -m pytest tests/ -v

test-cov: ## Run tests with coverage report
	$(PYTHON) -m pytest tests/ -v --cov=teb --cov-report=term-missing --cov-report=html

lint: ## Run linter (ruff)
	$(PYTHON) -m ruff check teb/ tests/

format: ## Format code (ruff)
	$(PYTHON) -m ruff format teb/ tests/
	$(PYTHON) -m ruff check --fix teb/ tests/

typecheck: ## Run type checker (mypy)
	$(PYTHON) -m mypy teb/ --ignore-missing-imports

check: lint typecheck test ## Run all checks (lint + typecheck + test)

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf htmlcov/ .coverage dist/ build/ *.egg-info

run: ## Start the development server
	$(PYTHON) -m uvicorn teb.main:asgi_app --reload --host 0.0.0.0 --port 8000

db-health: ## Check database health
	$(PYTHON) -c "from teb import storage; storage.init_db(); import json; print(json.dumps(storage.get_database_health(), indent=2))"
