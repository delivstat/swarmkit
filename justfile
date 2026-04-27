# Swael task runner. Run `just` with no args to list commands.

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


# Internal: run the cross-language schema demo for one artifact type.
_demo-schema artifact:
    @echo "── Python (swael-schema) ──"
    @uv run python scripts/demo_schema.py {{artifact}}
    @echo ""
    @echo "── TypeScript (@swael/schema) ──"
    @pnpm --silent --filter @swael/schema exec node scripts/demo-schema.mjs {{artifact}}

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
    @pnpm --silent --filter @swael/schema exec node scripts/codegen-types.mjs

# Drift check — regenerate and fail if the working tree is dirty. Used in CI.
schema-codegen-check: schema-codegen-py-check schema-codegen-ts-check

schema-codegen-py-check:
    uv run python scripts/codegen_pydantic.py
    @git diff --quiet --exit-code -- packages/schema/python/src/swael_schema/models || (echo "pydantic codegen drift detected — run 'just schema-codegen-py' and commit the result" && git --no-pager diff --stat -- packages/schema/python/src/swael_schema/models && exit 1)

schema-codegen-ts-check:
    @pnpm --silent --filter @swael/schema exec node scripts/codegen-types.mjs
    @git diff --quiet --exit-code -- packages/schema/typescript/src/types || (echo "ts codegen drift detected — run 'just schema-codegen-ts' and commit the result" && git --no-pager diff --stat -- packages/schema/typescript/src/types && exit 1)

# Show a typed object loaded through the generated pydantic models.
demo-codegen:
    @uv run python scripts/demo_codegen.py
    @echo ""
    @pnpm --silent --filter @swael/schema exec node scripts/demo-codegen.mjs

# Run `swael validate` against representative valid + invalid workspaces
# and show the output a real user would see. First-time UX sanity check for
# task #31 (the CLI) and task #23 (human-readable errors).
demo-validate:
    @uv run python scripts/demo_validate.py

# M1 exit demo — resolve the hello-swarm example end-to-end. The broken
# variant is expected to exit 1; the leading `-` keeps just from failing.
demo-resolver:
    @echo "── examples/hello-swarm/workspace (valid) ───────────────────"
    @uv run swael validate examples/hello-swarm/workspace --tree --no-color
    @echo ""
    @echo "── examples/hello-swarm/workspace-broken (deliberate typo) ──"
    -@uv run swael validate examples/hello-swarm/workspace-broken --no-color

# Show the size and the first 60 lines of the knowledge pack against the
# hello-swarm example. Confirms task #24 end-to-end without dumping the
# full ~350 KB pack to the terminal.
demo-knowledge-pack:
    @echo "── pack size (valid workspace overlay) ──"
    @uv run swael knowledge-pack examples/hello-swarm/workspace | wc -c
    @echo ""
    @echo "── pack head (broken workspace overlay) ──"
    @uv run swael knowledge-pack examples/hello-swarm/workspace-broken | head -60

# M3 exit demo — run the hello-swarm topology end-to-end. Uses whichever
# model provider env vars are set (SWARMKIT_PROVIDER + SWARMKIT_MODEL,
# or falls back to the agent's declared provider). The supervisor
# delegates to the greeter, and the greeter calls the hello-world MCP
# tool that's launched as a stdio subprocess by the runtime.
demo-run:
    @echo "── swael run (hello-swarm) ──"
    @uv run swael run examples/hello-swarm/workspace hello --input "Greet the engineering team" --no-color

# M6 exit demo — Code Review Swarm reviews a PR on delivstat/swael.
# Requires GITHUB_TOKEN + a model provider (SWARMKIT_PROVIDER/SWARMKIT_MODEL).
# The three-leader swarm (engineering, QA, ops) fetches PR data via
# GitHub MCP, analyses code quality + security + test coverage + deploy
# risk, and synthesises a final review verdict.
demo-code-review:
    @echo "── swael run (code-review-swarm) ──"
    @uv run swael run reference/ code-review --input "Review PR #49 on the repo delivstat/swael. Fetch the PR details and provide a code review." --no-color

# Build the Docker sandbox image for sandboxed MCP servers (design §8.8).
# Swarm-authored servers run inside this container with --network=none.
build-sandbox-image:
    docker build -t swael-mcp-sandbox docker/mcp-sandbox/

# Quickstart runtime CLI
run *args:
    uv run swael {{args}}

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
