# Runtm Development & Operations Makefile
# All targets delegate to docker compose -- no local Python required.
#
# Quick start:
#   cp infra/local.env.example .env
#   make dev

.DEFAULT_GOAL := help

DEV_COMPOSE  := docker compose -f infra/docker-compose.dev.yml
PROD_COMPOSE := docker compose -f infra/docker-compose.prod.yml
SVC          ?=

# ── Development ──────────────────────────────────────────────────────

.PHONY: dev
dev: ## Start the full stack with hot-reload (bind-mounted source)
	$(DEV_COMPOSE) up --build

.PHONY: dev-detach
dev-detach: ## Start the full stack in the background
	$(DEV_COMPOSE) up --build -d

.PHONY: down
down: ## Stop all services (dev + prod)
	$(DEV_COMPOSE) down 2>/dev/null; \
	$(PROD_COMPOSE) down 2>/dev/null; \
	true

.PHONY: restart
restart: ## Rebuild and restart dev stack
	$(DEV_COMPOSE) down
	$(DEV_COMPOSE) up --build

.PHONY: logs
logs: ## Tail logs (use SVC=api to filter, e.g. make logs SVC=worker)
	$(DEV_COMPOSE) logs -f $(SVC)

.PHONY: ps
ps: ## Show running services
	$(DEV_COMPOSE) ps

.PHONY: shell-api
shell-api: ## Open a shell inside the API container
	$(DEV_COMPOSE) exec api bash

.PHONY: shell-worker
shell-worker: ## Open a shell inside the worker container
	$(DEV_COMPOSE) exec worker bash

# ── Testing & Linting ────────────────────────────────────────────────

.PHONY: test
test: ## Run the full test suite inside Docker
	$(DEV_COMPOSE) exec api pytest /app/packages/shared/tests -v
	$(DEV_COMPOSE) exec api pytest /app/packages/api/tests -v

.PHONY: test-shared
test-shared: ## Run shared package tests
	$(DEV_COMPOSE) exec api pytest /app/packages/shared/tests -v

.PHONY: test-api
test-api: ## Run API package tests
	$(DEV_COMPOSE) exec api pytest /app/packages/api/tests -v

.PHONY: lint
lint: ## Lint with ruff
	$(DEV_COMPOSE) exec api ruff check /app/packages

.PHONY: format
format: ## Auto-format with ruff
	$(DEV_COMPOSE) exec api ruff format /app/packages

.PHONY: typecheck
typecheck: ## Run mypy type checks
	$(DEV_COMPOSE) exec api mypy /app/packages/shared/runtm_shared --ignore-missing-imports
	$(DEV_COMPOSE) exec api mypy /app/packages/api/runtm_api --ignore-missing-imports

# ── Database ─────────────────────────────────────────────────────────

.PHONY: db-migrate
db-migrate: ## Run alembic migrations
	$(DEV_COMPOSE) exec api alembic upgrade head

.PHONY: db-reset
db-reset: ## Destroy volumes and recreate from scratch
	$(DEV_COMPOSE) down -v
	$(DEV_COMPOSE) up --build

# ── Production (self-hosting) ────────────────────────────────────────

.PHONY: prod
prod: ## Start production stack (requires .env.prod with DATABASE_URL, REDIS_URL)
	$(PROD_COMPOSE) up --build -d

.PHONY: prod-down
prod-down: ## Stop production stack
	$(PROD_COMPOSE) down

.PHONY: prod-logs
prod-logs: ## Tail production logs
	$(PROD_COMPOSE) logs -f $(SVC)

# ── Health ───────────────────────────────────────────────────────────

.PHONY: health
health: ## Quick health check against local API
	@curl -sf http://localhost:8000/health | python -m json.tool 2>/dev/null || echo "API not reachable"

.PHONY: health-detailed
health-detailed: ## Detailed health (Postgres, Redis, queue depth)
	@curl -sf http://localhost:8000/health/detailed | python -m json.tool 2>/dev/null || echo "API not reachable"

# ── Help ─────────────────────────────────────────────────────────────

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
