# Contributing to SwarmKit

Every change goes through a pull request — code, docs, workspace files, design notes. Branch
protection is enforced on `main` for everyone, including admins.

## Setup

Prerequisites: Python 3.11+, Node 20+, [`uv`](https://docs.astral.sh/uv/), `pnpm`, `just`.

```bash
git clone git@github.com:delivstat/swarmkit.git && cd swarmkit
just install     # uv sync + pnpm install
just test        # pytest + vitest
```

Run `just` with no arguments to list every task.

## Verify the way CI does — which is repo-wide

This is the single most common cause of a red PR after a green local run:

```bash
uv run ruff check .                  # the whole repo, not just your package
uv run ruff format --check .
uv run mypy packages/runtime packages/schema/python packages/control-plane
uv run pytest packages/runtime/tests packages/schema/python/tests -q
pnpm -r run lint && pnpm -r test
```

Two traps worth naming, because both have bitten:

- **`mypy` includes tests.** A helper without a return annotation in a test file fails CI. Checking
  only `src/` passes locally and fails on the PR.
- **`ruff format --check .` covers `scripts/` and `examples/`.** Formatting only `packages/` leaves
  a file behind.

`mypy` reads `strict = true` from the root `pyproject.toml`, so the flag is implied.

## The delivery workflow

Every feature, big or small. No exceptions.

**1. Design first.** A short note at `design/details/<feature-slug>.md` stating goal, non-goals, API
shape, test plan, demo plan — or a reference to the section of `design/SwarmKit-Design-v0.6.md` that
already covers it. For anything large, open a **design-only PR** and get agreement before writing
code. It is much cheaper to argue about a document.

**2. Branch.** `feat/<scope>-<slug>`, `fix/<scope>-<slug>`, `design/<slug>`, `docs/<slug>`. One
feature per branch.

**3. Tests are mandatory.** Unit, integration, or both. No "tests later" — a PR without tests is not
reviewable.

Write the test that would have caught the bug, not the test that passes. If a defect could recur
silently, the test's job is to make the recurrence loud: assert on the property, and say in the
docstring what it costs when it breaks.

**4. Demo every feature.** A runnable script under `examples/`, a `just demo-<feature>` target, a
recorded terminal transcript in the PR body, or a screenshot for UI work. The demo proves the
feature works for a person, not just for the compiler.

Prefer demos that need nothing installed. `examples/command-packs/demo.py` runs every command
through `python3` for exactly this reason.

**5. PR against `main`.** Link the design note, show the demo, summarise test coverage. Template at
`.github/pull_request_template.md`.

**6. Green CI, then merge.** Delete the branch afterwards.

## Discipline notes — read before a cross-cutting change

`docs/notes/` holds the "remember to also touch Y when you change X" rules. They exist because each
one was learned by shipping the bug.

| note | when |
| --- | --- |
| [`schema-change-discipline.md`](docs/notes/schema-change-discipline.md) | **any** edit under `packages/schema/schemas/` |
| [`release-version-discipline.md`](docs/notes/release-version-discipline.md) | before cutting a release |
| [`usability-first.md`](docs/notes/usability-first.md) | anything a user configures or reads |
| [`llm-friendly-knowledge.md`](docs/notes/llm-friendly-knowledge.md) | docs — they are consumed by models as much as people |

If your change introduces a new such rule, add a note. A rule you have to remember is not a rule.

**Schema changes have a checklist and skipping one step is silent.** The Python validator prefers
the bundled copy under `packages/schema/python/src/swarmkit_schema/_schemas/`, so an un-synced copy
means tests validate against the *old* shape and pass.

## Invariants

`CLAUDE.md` holds the full list. The ones that most often come up in review:

1. **Topology is data.** The runtime interprets YAML; it does not generate Python.
2. **Skills are the only capability extension primitive** — four backings (`mcp_tool`, `llm_prompt`,
   `composed`, `command`), one noun. Node *execution* is a separate seam: the `executor`
   abstraction, where `model` and `harness` are kinds.
3. **Governance goes through `GovernanceProvider`**; only `governance/` imports AGT.
4. **LLM calls go through `ModelProvider`**; only `model_providers/` imports a vendor SDK.
5. **The audit log has no update or delete path** exposed to the executive side. Not "we don't call
   it" — there isn't one.
6. **Approval gates are structural.** Reserved scopes (`skills:activate`, `mcp_servers:deploy`,
   `topologies:modify`, `iam:modify`, `approvals:resolve`) are enforced by the policy engine and
   cannot be granted to an agent regardless of prompt.

## Commit and PR style

`type(scope): subject`. Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `design`. Scopes
are package names (`runtime`, `schema`, `ui`) or areas (`topology`, `skills`, `governance`, `cli`).

```
feat(runtime): load topology YAML and validate against schema
fix(governance): readonly consults a declared effect, not the tool's name
design(v0.7): resolve §21 sandboxing question
```

**Write the body for whoever hits this in six months.** State what was wrong, what it cost, and why
the fix is shaped the way it is — not a restatement of the diff, which git already has. A commit
that says *why* is the difference between a fix and a fix somebody can trust.

**Authorship:** commit messages and PR bodies carry delivstat authorship only. No `Co-Authored-By:`
trailers, no generated-with footers.

## Releasing

See [`RELEASING.md`](RELEASING.md). Short version: bump every package whose content changed, commit,
`just release-check`, build, tag with a message that reads like a changelog entry, push, then
regenerate the changelog in a follow-up PR.

## Reporting a bug

Open an issue with what you ran, what happened, and what you expected. A version (`swarmkit
--version`, or `/health` if you are on `serve`) turns a guess into a diagnosis.

Reports that name the mechanism are exceptionally useful and have directly changed this codebase —
the stale portal bundle in 1.199.0 was found and diagnosed by a user, not by CI.
