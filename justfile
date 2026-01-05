set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
    @just --list

sync:
    uv sync --group dev

lint:
    uv run ruff check .
    uv run mypy cli/djangoops tests

format:
    uv run ruff format .
    uv run ruff check --fix .

format-check:
    uv run ruff format --check .

test:
    uv run pytest

check: lint format-check test
