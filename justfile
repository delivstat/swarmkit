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

# Internal: run the cross-language schema demo for one artifact type.
_demo-schema artifact:
    @echo "── Python (swarmkit-schema) ──"
    @uv run python scripts/demo_schema.py {{artifact}}
    @echo ""
    @echo "── TypeScript (@swarmkit/schema) ──"
    @pnpm --silent --filter @swarmkit/schema exec node scripts/demo-schema.mjs {{artifact}}

# Per-artifact demos (valid + invalid fixtures in both Python and TS).
demo-topology-schema:   (_demo-schema "topology")
demo-skill-schema:      (_demo-schema "skill")
demo-archetype-schema:  (_demo-schema "archetype")
demo-workspace-schema:  (_demo-schema "workspace")
demo-trigger-schema:    (_demo-schema "trigger")

# Aggregate: run every per-artifact demo. Exit criterion for Milestone 0 —
# all five schemas exercised in both Python and TypeScript against committed
# valid + invalid fixtures.
demo-schema: demo-topology-schema demo-skill-schema demo-archetype-schema demo-workspace-schema demo-trigger-schema

# Regenerate both pydantic models and TypeScript types from the canonical
# JSON Schemas. Run after any schema edit per
# docs/notes/schema-change-discipline.md.
schema-codegen: schema-codegen-py schema-codegen-ts

schema-codegen-py:
    uv run python scripts/codegen_pydantic.py

schema-codegen-ts:
    @pnpm --silent --filter @swarmkit/schema exec node scripts/codegen-types.mjs

# Drift check — regenerate and fail if the working tree is dirty. Used in CI.
schema-codegen-check: schema-codegen-py-check schema-codegen-ts-check

schema-codegen-py-check:
    uv run python scripts/codegen_pydantic.py
    @git diff --quiet --exit-code -- packages/schema/python/src/swarmkit_schema/models || (echo "pydantic codegen drift detected — run 'just schema-codegen-py' and commit the result" && git --no-pager diff --stat -- packages/schema/python/src/swarmkit_schema/models && exit 1)

schema-codegen-ts-check:
    @pnpm --silent --filter @swarmkit/schema exec node scripts/codegen-types.mjs
    @git diff --quiet --exit-code -- packages/schema/typescript/src/types || (echo "ts codegen drift detected — run 'just schema-codegen-ts' and commit the result" && git --no-pager diff --stat -- packages/schema/typescript/src/types && exit 1)

# Show a typed object loaded through the generated pydantic models.
demo-codegen:
    @uv run python scripts/demo_codegen.py
    @echo ""
    @pnpm --silent --filter @swarmkit/schema exec node scripts/demo-codegen.mjs

# Run `swarmkit validate` against representative valid + invalid workspaces
# and show the output a real user would see. First-time UX sanity check for
# task #31 (the CLI) and task #23 (human-readable errors).
demo-validate:
    @uv run python scripts/demo_validate.py

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
