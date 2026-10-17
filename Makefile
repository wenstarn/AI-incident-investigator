.PHONY: install lint format test smoke-test db-up db-down redis-up redis-down db-migrate db-revision order-db-migrate order-db-revision order-db-seed run-user run-order run-frontend up down build logs ps

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
