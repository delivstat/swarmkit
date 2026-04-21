# CI pipeline

**Scope:** workspace
**Design reference:** IMPLEMENTATION-PLAN.md cross-cutting workstreams
**Status:** approved (this PR)

## Goal

Every PR and every `main` push runs lint + typecheck + tests for Python and TypeScript, on every supported Python version, and fails fast on any regression. The pipeline is the "green checkmark" signal that lets every subsequent milestone's exit demo say "done."

## Non-goals

- Deployment automation (PyPI / npm publish) — that's Milestone 10.
- End-to-end tests against real LLM APIs — wait for Milestone 4 where judges arrive.
- Code coverage reporting — add later once meaningful code exists (after M1).
- Release note generation — Milestone 10.

## Design reference

From `CLAUDE.md` § Tooling: Python via `uv`, TS via `pnpm`, orchestrated by `just`. Matrix on Python 3.11, 3.12, 3.13 (per `pyproject.toml` `requires-python`). Node 20 is the baseline.

Design doc §20.2 anticipates GitHub as the primary distribution channel, so GitHub Actions is the natural home for CI.

## Jobs

Three parallel jobs, all required for merge:

1. **`python`** — runs on `ubuntu-latest`, matrix over Python 3.11 / 3.12 / 3.13.
   - `uv sync --all-packages --group dev`
   - `uv run ruff check .`
   - `uv run mypy packages/runtime packages/schema/python`
   - `uv run pytest`
2. **`javascript`** — runs on `ubuntu-latest`, Node 20.
   - `pnpm install --frozen-lockfile`
   - `pnpm -r run lint`
   - `pnpm -r run typecheck`
   - `pnpm -r run test`
3. **`schema-validity`** — runs on `ubuntu-latest`, checks every `*.schema.json` is valid Draft 2020-12. Cheap, catches schema editing bugs instantly.

Caching:
- `actions/setup-python` caches pip/uv automatically via `setup-uv` action.
- `pnpm/action-setup` + `actions/cache` on the pnpm store.

Triggers:
- `push` to `main`
- `pull_request` targeting `main`

## Test plan

- **Test:** the first run of this workflow on the PR itself is the test. It must pass with all three jobs green before merge.
- **Regression coverage:** the existing 9 Python tests + 2 TS tests exercise the scaffold's import/validation paths. Enough to detect a broken workspace.
- **Negative case:** confirmed locally that introducing a ruff violation or a schema typo fails the corresponding job.

## Demo plan

The PR's own Actions tab showing all three jobs green on the first run is the demo. Screenshot or link in the PR body.

## Open questions

- Should we add Windows + macOS to the Python matrix? Recommendation: no for now. Add when community demand appears. AGT Python SDK is Linux-primary; contributors run Linux or macOS. Revisit at M10.
- Should we gate on coverage threshold? Recommendation: no until after M3 when real logic lands. Meaningless for scaffolded code.

## Bootstrap scaffold fixes landing with this PR

The initial scaffold had small issues that only surface once CI actually runs. Fixing them here rather than in separate PRs so the first CI run is green:

- Duplicate `tests/__init__.py` caused pytest package-name collision — removed (pytest doesn't need them).
- Schema validator `importlib.resources` path didn't resolve in editable installs — now tries installed package first, falls back to the canonical repo-level dir.
- `uv sync` wasn't installing pytest/mypy/ruff — moved to root `[dependency-groups] dev` (PEP 735).
- TS schema import path was `../schemas/` (post-copy location) but `copy-schemas.mjs` hadn't run — changed to `../../schemas/` for dev; publish-time copy handled at M10.
- Ajv v8 default doesn't support draft 2020-12; switched to `ajv/dist/2020`.
- UI package had no source yet; lint/typecheck/test would fail spuriously. Added a `noop.mjs` that exits 0 when empty and errors when source appears (reminder to restore real scripts).
- Mypy `no-any-return` on `json.loads()`; annotated the intermediate variable.
- Ruff rules required top-level imports in tests and hit an en-dash in a docstring; fixed both.
