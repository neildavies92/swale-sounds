.PHONY: help setup format lint typecheck test check migrate smoke clean

.DEFAULT_GOAL := help

help:
	@printf '%s\n' 'Swale Sounds development commands' '' \
	  '  make setup      Sync Python dependencies' \
	  '  make format     Format Python code' \
	  '  make lint       Run formatting and lint checks' \
	  '  make typecheck  Run strict mypy' \
	  '  make test       Run pytest' \
	  '  make check      Run all quality checks' \
	  '  make migrate    Upgrade the local database' \
	  '  make smoke      Run the isolated Phase 1 end-to-end smoke test' \
	  '  make clean      Remove development tool caches'

setup:
	uv sync

format:
	uv run ruff format .

lint:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	uv run mypy src

test:
	uv run pytest

check: lint typecheck test

migrate:
	uv run alembic upgrade head

smoke:
	uv run python scripts/smoke_phase1.py

clean:
	rm -rf -- .pytest_cache .mypy_cache .ruff_cache
