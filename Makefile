# Convenience targets. On Windows use Git Bash, WSL, or run the commands directly.

.PHONY: install migrate api worker beat scheduler test lint frontend up down

install:
	pip install -e ".[dev]"

migrate:
	alembic upgrade head

api:
	uvicorn app.main:app --reload --app-dir backend

worker:
	celery -A app.core.celery_app.celery worker -l info

beat:
	celery -A app.core.celery_app.celery beat -l info

scheduler:
	python -m app.scheduler.jobs

test:
	pytest -q

lint:
	ruff check backend tests

frontend:
	cd frontend && npm install && npm run dev

up:
	docker compose up -d --build

down:
	docker compose down
