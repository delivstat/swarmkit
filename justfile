# SwarmKit task runner. Run `just` with no args to list commands.

set shell := ["bash", "-uc"]
set dotenv-load := true

default:
    @just --list

# ---- Install ----

install: install-py install-js
    @echo "All dependencies installed."

install-py:
    uv sync --all-packages

install-js:
    pnpm install

# ---- Lint / format / typecheck ----

lint: lint-py lint-js

lint-py:
    uv run ruff check .

lint-js:
    pnpm run lint

format:
    uv run ruff format .
    pnpm run format

typecheck: typecheck-py typecheck-js

typecheck-py:
    uv run mypy packages/runtime packages/schema/python

typecheck-js:
    pnpm run typecheck

# ---- Test ----

test: test-py test-js

test-py:
    uv run pytest

test-js:
    pnpm run test

# ---- Build ----

build: build-py build-js

build-py:
    uv build --all-packages

build-js:
    pnpm run build

# ---- Runtime helpers ----

# Regenerate the markdown extraction of the design docx
design-extract:
    uv run python scripts/extract_design.py

# Quickstart runtime CLI (once implemented)
run *args:
    uv run swarmkit {{args}}

# ---- Cleanup ----

clean:
    find . -type d -name __pycache__ -prune -exec rm -rf {} +
    find . -type d -name .pytest_cache -prune -exec rm -rf {} +
    find . -type d -name .mypy_cache -prune -exec rm -rf {} +
    find . -type d -name .ruff_cache -prune -exec rm -rf {} +
    find . -type d -name node_modules -prune -exec rm -rf {} +
    find . -type d -name .next -prune -exec rm -rf {} +
    find . -type d -name dist -prune -exec rm -rf {} +
    find . -type d -name build -prune -exec rm -rf {} +
