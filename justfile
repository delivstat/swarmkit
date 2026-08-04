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

# Serial run, for when a parallel failure might be order-dependent.
test-py-serial:
    uv run pytest -p no:xdist

test-js:
    pnpm run test

# Control-plane OIDC-login browser e2e (Playwright). Not in CI — run locally. Extra args pass
# through to `playwright test`, e.g. `just e2e --headed`.
e2e *args:
    @scripts/e2e.sh {{args}}

# ---- Build ----

build: build-py build-js

build-py:
    uv build --all-packages

build-js:
    pnpm run build

# Build the web portal static export and stage it into the swarmkit-webui package (serve-hosted-webui.md).
# `swarmkit serve` then hosts it when swarmkit-runtime[ui] is installed. Assets are generated, not committed.
build-webui:
    pnpm --filter @swarmkit/ui build
    rm -rf packages/webui/src/swarmkit_webui/_static
    cp -r packages/ui/out packages/webui/src/swarmkit_webui/_static
    touch packages/webui/src/swarmkit_webui/_static/.gitkeep
    @echo "staged portal → packages/webui/src/swarmkit_webui/_static ($(find packages/webui/src/swarmkit_webui/_static -type f | wc -l) files)"

# ---- Runtime helpers ----


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
demo-role-registry-schema: (_demo-schema "role-registry")
demo-approval-policy-schema: (_demo-schema "approval-policy")

# Aggregate: run every per-artifact demo. Exit criterion for Milestone 0 —
# all core schemas exercised in both Python and TypeScript against committed
# valid + invalid fixtures.
demo-schema: demo-topology-schema demo-skill-schema demo-archetype-schema demo-workspace-schema demo-trigger-schema demo-role-registry-schema demo-approval-policy-schema

# Demo output_schema on a harness executor (harness-output-schema.md): markdown against a declared
# schema, corrected through the harness; and a schema-less worker left alone.
demo-harness-output-schema:
    uv run python packages/runtime/demos/harness_output_schema.py

# Demo decision skills on a harness executor (harness-decision-skills.md): a `required: true`
# spec check that never ran on a harness, now failing markdown and re-invoking the harness.
demo-harness-decision-skills:
    uv run python packages/runtime/demos/harness_decision_skills.py

# Demo a named local operator (none-auth-named-operator.md): the same gate, refused for `anonymous`
# and resolvable for a named operator — identical authority, only the name differs.
demo-named-operator:
    uv run python packages/runtime/demos/named_local_operator.py

# Demo harness tool outcomes (harness-tool-outcomes.md): one FAILING tool call per bundled harness,
# in each harness's native protocol — traced as a checkmark before the fix, as an error after.
demo-harness-tool-outcomes:
    uv run python packages/runtime/demos/harness_tool_outcomes.py

# Demo the gate funnel (slice 3): the validate->judge->approve subgraph + structural invariant,
# real multi-party approval, bounded retry, and retry-exhaustion escalation.
demo-gate-funnel:
    uv run python packages/runtime/demos/gate_funnel.py

# Demo who may resolve a role-task (pipeline-gate-approval-ui.md, slice 1): the resolver is the
# authenticated caller checked against the role registry — a body-supplied identity buys nothing.
demo-review-resolve:
    uv run python packages/runtime/demos/review_resolve_identity.py

# Demo role-task serialization + gate detail (pipeline-gate-approval-ui.md, slice 2): the fourth
# review kind, the kind/gate filters, and gate-status reporting the quorum the engine decided.
demo-role-task:
    uv run python packages/runtime/demos/role_task_serialization.py

# Demo slice_budget wired as a funnel validate layer (funnel-deterministic-validate.md): an
# over-budget diff drives the funnel's bounded retry, then escalates to the human approve.
demo-funnel-validate:
    uv run python packages/runtime/demos/funnel_slice_budget.py

# Demo cited_change wired as a funnel validate layer (funnel-deterministic-validate.md, step 2):
# the produced diff is threaded to the gate; an uncited change drives the retry, then escalates.
demo-funnel-cited-change:
    uv run python packages/runtime/demos/funnel_cited_change.py

# Demo governed memory (governed-memory.md, slice 1): a fact evolves in place over time — new /
# reinforce / update on one canonical row + an append-only change-log with a point-in-time read.
demo-governed-memory:
    uv run python packages/runtime/demos/governed_memory.py

# Demo the knowledge-curator reference topology + the memory-reconcile decision skill
# (governed-memory.md): a scripted skill output drives refine/contradict through the real path.
demo-knowledge-curator:
    uv run python packages/runtime/demos/knowledge_curator.py

# Demo the governed-memory persistence skill on a live compiled run (governed-memory.md): an agent
# carrying the skill emits candidates; the compiler hook writes them through the governed path.
demo-governed-memory-run:
    uv run python packages/runtime/demos/governed_memory_run.py

# Demo relevance-ranked retrieval for governed memory (governed-memory.md): TF-IDF by default,
# cosine similarity when an embedder is wired — queries surface the most relevant facts.
demo-governed-memory-search:
    uv run python packages/runtime/demos/governed_memory_search.py

# Demo the `swarmkit memory` CLI (governed-memory.md): search, get --history, quarantine, resolve —
# the same store + JSON as the serve /memory endpoints (one service seam).
demo-governed-memory-cli:
    uv run python packages/runtime/demos/governed_memory_cli.py

# Demo gate coverage (gate-coverage-and-comprehension-debt.md, slice 1): the narrowest verified
# edge of the full SDLC pipeline — every stage classified passthrough | human(+pre-filters), the
# weakest edge named. Read-only, no keys/server. `--require human` (added) exits 1 on any passthrough.
demo-gates:
    uv run swarmkit gates examples/sdlc-pipeline/workspace --pipeline sdlc-full

# Demo the recurring expert-persona repo audit (slice 6): a cron Trigger fires a read-only
# expert-reviewer panel (5 lenses). Prints the schedule → panel wiring; no model calls, no server.
demo-repo-audit:
    uv run python examples/sdlc-pipeline/demo_repo_audit.py

# Demo comprehension-debt telemetry (slice 3): fast-approve signals from the audit log (report-only,
# never a gate), plus the disclosed deferred signals. Reads the example's .swarmkit audit store.
demo-comprehension:
    uv run swarmkit comprehension examples/sdlc-pipeline/workspace

# Demo cited-change (slice 5): the deterministic citation check — does a change-rationale cite the
# code its diff actually changed? Exits 1 on an uncited change (CI-gatable). No keys/server.
demo-cited-change:
    uv run swarmkit cited-change --rationale examples/sdlc-pipeline/fixtures/change-rationale.yaml --diff examples/sdlc-pipeline/fixtures/change.diff

# Demo slice-check (slice 7): measure a diff against a slice budget — keep changes reviewable.
# Exits 1 when over budget (CI-gatable). No keys/server.
demo-slice-check:
    uv run swarmkit slice-check --diff examples/sdlc-pipeline/fixtures/change.diff --max-diff-lines 400 --max-files 20

# Demo the FULL SDLC lifecycle (slice 9, the capstone): the reference controller drives one
# requirement through the ENTIRE pipeline — intake -> design -> build -> sit -> pt ->
# security-review -> deploy -> support-handover -> done — carrying the two multi-party gates, the
# final release sign-off (eng-manager + cio), the contract locks, and the defect loop. Prints the
# full correlated saga timeline. Deterministic (scripted run_stage seam, mock rigs, no keys/server).
demo-sdlc:
    uv run python examples/sdlc-pipeline/demo_full_sdlc.py

# Demo the one-app (OMS) bounded stage run (slice 4): intake -> design -> judge -> approval, with
# IAM scoping + the gate funnel + the agent-determination-only shape.
demo-sdlc-stage-run:
    uv run python examples/sdlc-pipeline/demo_oms_stage_run.py

# Demo the consolidated design across all three apps (slice 6): three per-app solution architects
# feed the integration-architect synthesizer, and the four-layer consolidated-design-approval funnel
# runs — validate -> judge -> the architect-reviewer harness review -> multi-party approval — with a
# route-back on a HIGH-severity harness finding. Deterministic, faked seams (no keys, no server).
demo-consolidated-design:
    uv run python examples/sdlc-pipeline/demo_consolidated_design.py

# Demo the harness build (slice 7) — the executor showcase: the `developer` HARNESS executor
# (`claude-code`, sandboxed) produces a candidate diff against a demo repo, and the `oms-code-review`
# funnel judges it — a clean review advances, a finding routes the critique back to the harness for a
# bounded revision before the OMS lead signs off. Deterministic: the real bundled claude-code adapter
# translates a scripted stream-json transcript; only the subprocess launch is faked (no keys/network).
demo-harness-build:
    uv run python examples/sdlc-pipeline/demo_harness_build.py

# Demo the controller-driven defect loop (slice 8) — the centerpiece: the reference controller
# sequences the sdlc-sit-pt stage-graph and a defect found in SIT re-kicks build (defect.raised) and
# its fix re-triggers SIT (defect.fixed); the re-run passes and the saga proceeds through PT + the
# pre-release security gate to done. Deterministic (scripted run_stage seam, mock rigs, no server).
demo-defect-loop:
    uv run python examples/sdlc-pipeline/demo_defect_loop.py

# Demo cross-app SIT + PT against mock rigs + the pre-release security review (slice 8): the sit-qa
# e2e flows across oms/web/mobile, the pt-engineer's perf test judged by pt-analysis, and the
# security-review-approval funnel — the security-consultant harness review (HIGH finding routes back)
# then the infosec-lead sign-off. Deterministic, faked seams (no keys, no network, no server).
demo-sit-pt:
    uv run python examples/sdlc-pipeline/demo_sit_pt.py

# Demo the OMS pipeline driven by the Temporal orchestrator (orchestration-provider-seam.md).
# The orchestrator group (temporalio) is pulled in on demand by `uv run --group orchestrator` —
# no separate sync, and (this is a virtual uv workspace) no pruning of the workspace members.
demo-pipeline-temporal:
    uv run --group orchestrator python examples/sdlc-pipeline/demo_pipeline_temporal.py

# Demo the SDLC pipeline controller (slice 5): a saga sequenced across intake -> design (gate) ->
# build -> sit over a scripted run_stage seam — event dedup, dropped-event reconciliation,
# per-contract lock serialisation, and cancellation with reverse-order compensation.
demo-pipeline-controller:
    uv run python examples/sdlc-pipeline/demo_pipeline_controller.py

# Demo the webhook -> pipeline ingress path (37c, pipeline-triggering.md): a signed CI webhook
# emits build.ready-in-qa on the ingress front door and the reference saga advances build -> sit;
# an unauthorised skip is denied (403) and audited. In-process, no live server, no model calls.
demo-pipeline-trigger:
    uv run python examples/sdlc-pipeline/demo_pipeline_trigger.py

# Demo the published fleet-enrollment protocol schemas (register/join + InstanceState, design 19):
# validate every committed protocol fixture + show the cross-file $ref enforcing nested state.
demo-protocol-schema:
    uv run python scripts/demo_protocol_schema.py

# Demo fleet identity (design 21): the register proof-of-possession handshake + TOFU pinning +
# rogue rejection, end to end against a live serve.
demo-fleet-identity:
    uv run python scripts/demo_fleet_identity.py

# Demo signed pushes (design 22): a fleet-signed deploy is applied; a tampered payload, an unsigned
# deploy (when required), or a replayed old signed deploy (downgrade guard) is rejected.
demo-signed-deploy:
    uv run python scripts/demo_signed_deploy.py

# Regenerate both pydantic models and TypeScript types from the canonical
# JSON Schemas. Run after any schema edit per
# docs/notes/schema-change-discipline.md.
# Regenerate llms-full.txt (the expanded single-file LLM corpus) + its published copy.
llms-full:
    uv run python scripts/build_llms_full.py

# Regenerate the changelog from the annotated git tags. Run after tagging a release.
changelog:
    uv run python scripts/build_changelog.py

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

# Generate the control-plane UI's KNOWN_VERBS from the panel's canonical VERB_TIERS, then
# biome-format so the committed file is byte-stable against the linter.
codegen-verbs:
    uv run python scripts/codegen_verbs.py
    @pnpm --silent --filter @swarmkit/control-plane-ui exec biome format --write lib/generated/verbs.ts

# Drift check — regenerate the verb contract and fail if the working tree is dirty. Used in CI.
codegen-verbs-check: codegen-verbs
    @git diff --quiet --exit-code -- packages/control-plane-ui/lib/generated/verbs.ts || (echo "verb-contract codegen drift detected — run 'just codegen-verbs' and commit the result" && git --no-pager diff -- packages/control-plane-ui/lib/generated/verbs.ts && exit 1)

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

# M1 exit demo — resolve the hello-swarm example end-to-end. The broken
# variant is expected to exit 1; the leading `-` keeps just from failing.
demo-resolver:
    @echo "── examples/hello-swarm/workspace (valid) ───────────────────"
    @uv run swarmkit validate examples/hello-swarm/workspace --tree --no-color
    @echo ""
    @echo "── examples/hello-swarm/workspace-broken (deliberate typo) ──"
    -@uv run swarmkit validate examples/hello-swarm/workspace-broken --no-color

# Show the size and the first 60 lines of the knowledge pack against the
# hello-swarm example. Confirms task #24 end-to-end without dumping the
# full ~350 KB pack to the terminal.
demo-knowledge-pack:
    @echo "── pack size (valid workspace overlay) ──"
    @uv run swarmkit knowledge-pack examples/hello-swarm/workspace | wc -c
    @echo ""
    @echo "── pack head (broken workspace overlay) ──"
    @uv run swarmkit knowledge-pack examples/hello-swarm/workspace-broken | head -60

# M3 exit demo — run the hello-swarm topology end-to-end. Uses whichever
# model provider env vars are set (SWARMKIT_PROVIDER + SWARMKIT_MODEL,
# or falls back to the agent's declared provider). The supervisor
# delegates to the greeter, and the greeter calls the hello-world MCP
# tool that's launched as a stdio subprocess by the runtime.
demo-run:
    @echo "── swarmkit run (hello-swarm) ──"
    @uv run swarmkit run examples/hello-swarm/workspace hello --input "Greet the engineering team" --no-color

# M6 exit demo — Code Review Swarm reviews a PR on delivstat/swarmkit.
# Requires GITHUB_TOKEN + a model provider (SWARMKIT_PROVIDER/SWARMKIT_MODEL).
# The three-leader swarm (engineering, QA, ops) fetches PR data via
# GitHub MCP, analyses code quality + security + test coverage + deploy
# risk, and synthesises a final review verdict.
demo-code-review:
    @echo "── swarmkit run (code-review-swarm) ──"
    @uv run swarmkit run reference/ code-review --input "Review PR #49 on the repo delivstat/swarmkit. Fetch the PR details and provide a code review." --no-color

# Build the Docker sandbox image for sandboxed MCP servers (design §8.8).
# Swarm-authored servers run inside this container with --network=none.
build-sandbox-image:
    docker build -t swarmkit-mcp-sandbox docker/mcp-sandbox/

# Quickstart runtime CLI
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
