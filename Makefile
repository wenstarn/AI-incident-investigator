.PHONY: install lint format test smoke-test db-up db-down redis-up redis-down db-migrate db-revision order-db-migrate order-db-revision order-db-seed run-user run-order run-frontend up down build logs ps incident-list incident-activate incident-status incident-reset incident-logs incident-test

ifneq (,$(wildcard ./.env))
include .env
export
endif

install:
	uv sync --all-packages

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

test:
	uv run pytest
	cd services/incident-controller && uv run pytest

smoke-test:
	docker compose up --build -d
	docker compose restart frontend
	uv run --package order-service python tests/smoke_test.py

db-up:
	docker compose up -d postgres

db-down:
	docker compose stop postgres

redis-up:
	docker compose up -d redis

redis-down:
	docker compose stop redis

db-migrate:
	uv run --package user-service alembic -c services/user-service/alembic.ini upgrade head

db-revision:
	uv run --package user-service alembic -c services/user-service/alembic.ini revision --autogenerate -m "$(MESSAGE)"

order-db-migrate:
	uv run --package order-service alembic -c services/order-service/alembic.ini upgrade head

order-db-revision:
	uv run --package order-service alembic -c services/order-service/alembic.ini revision --autogenerate -m "$(MESSAGE)"

order-db-seed:
	uv run --package order-service python -m order_service.seed

run-user:
	uv run --package user-service uvicorn user_service.main:app --port 8001

run-order:
	uv run --package order-service uvicorn order_service.main:app --port 8002

run-frontend:
	python3 -m http.server 3000 --directory frontend

up:
	docker compose up --build -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

ps:
	docker compose ps

# Token is expanded by the shell, so make never prints it.
incident-list:
	@set -eu; test -n "$$INCIDENT_CONTROLLER_TOKEN" || { echo 'Set INCIDENT_CONTROLLER_TOKEN in .env' >&2; exit 1; }; curl --fail-with-body --silent --show-error -H "X-Incident-Token: $$INCIDENT_CONTROLLER_TOKEN" http://127.0.0.1:8003/incidents; echo

incident-activate:
	@set -eu; test -n "$(ID)" || { echo 'Usage: make incident-activate ID=INC-001' >&2; exit 1; }; case "$(ID)" in INC-001|INC-002|INC-003|INC-004|INC-005|INC-006) ;; *) echo 'Unknown incident ID' >&2; exit 1;; esac; test -n "$$INCIDENT_CONTROLLER_TOKEN" || { echo 'Set INCIDENT_CONTROLLER_TOKEN in .env' >&2; exit 1; }; curl --fail-with-body --silent --show-error -X POST -H "X-Incident-Token: $$INCIDENT_CONTROLLER_TOKEN" "http://127.0.0.1:8003/incidents/$(ID)/activate"; echo

incident-status:
	@set -eu; test -n "$$INCIDENT_CONTROLLER_TOKEN" || { echo 'Set INCIDENT_CONTROLLER_TOKEN in .env' >&2; exit 1; }; curl --fail-with-body --silent --show-error -H "X-Incident-Token: $$INCIDENT_CONTROLLER_TOKEN" http://127.0.0.1:8003/incidents/active; echo

incident-reset:
	@set -eu; test -n "$$INCIDENT_CONTROLLER_TOKEN" || { echo 'Set INCIDENT_CONTROLLER_TOKEN in .env' >&2; exit 1; }; curl --fail-with-body --silent --show-error -X POST -H "X-Incident-Token: $$INCIDENT_CONTROLLER_TOKEN" http://127.0.0.1:8003/incidents/active/reset; echo

incident-logs:
	docker compose logs -f incident-controller

incident-test:
	@echo 'WARNING: incident tests temporarily stop User Service, Redis, and PostgreSQL.'
	cd services/incident-controller && uv run pytest -m destructive tests/test_integration.py

# Destructive integration checks are deliberately separate from make test.
diagnostic-test:
	@echo 'WARNING: diagnostic tests temporarily stop User Service, Redis, and PostgreSQL.'
	docker compose --profile diagnostic-test run --build --rm diagnostic-test-runner

# Advanced dependency fault checks are also destructive and remain opt-in.
diagnostic-test-advanced:
	@echo 'WARNING: advanced diagnostic tests activate INC-004 through INC-006.'
	docker compose --profile diagnostic-test run --build --rm diagnostic-test-runner python tests/diagnostic_advanced_integration.py
