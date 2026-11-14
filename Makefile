# PitWall developer commands. Run `make help` to list them.
SHELL := /bin/bash
.DEFAULT_GOAL := help
UV ?= uv
COMPOSE ?= docker compose

.PHONY: help install lint format typecheck test test-integration check fixtures up down logs clean dagster

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies and git hooks
	$(UV) sync --all-extras
	$(UV) run pre-commit install

lint: ## Lint and check formatting
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format: ## Auto-fix lint issues and format code
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

typecheck: ## Strict mypy on src/
	$(UV) run mypy

test: ## Unit tests (offline, recorded fixtures) with coverage
	$(UV) run pytest

test-integration: ## Live tests against the real OpenF1 API
	PITWALL_RUN_INTEGRATION=1 $(UV) run pytest tests/integration --no-cov

check: lint typecheck test ## Everything CI runs, except the Docker build

fixtures: ## Re-record OpenF1 fixtures from the live API
	$(UV) run pitwall-record-fixtures

dagster: ## Run Dagster locally without Docker (http://localhost:3000)
	DAGSTER_HOME=$(CURDIR)/.dagster_home $(UV) run dagster dev -m pitwall.orchestration.definitions

up: ## Start S3 storage and Dagster with Docker Compose
	@test -f .env || cp .env.example .env
	$(COMPOSE) up -d --build
	@echo "Dagster: http://localhost:3000   S3 API: http://localhost:9000"

down: ## Stop the stack (data volumes are kept)
	$(COMPOSE) down

logs: ## Follow container logs
	$(COMPOSE) logs -f

clean: ## Remove caches and local data (keeps .env)
	rm -rf .mypy_cache .ruff_cache .pytest_cache .coverage htmlcov data .dagster_home
