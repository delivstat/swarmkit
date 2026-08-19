# Extracting Minder and Vedanta Advisor into their own repos

**Status:** Minder extracted — `delivstat/minder` (private), 88 commits and all 17 branches, live
and verified. Vedanta remains.

Minder and Vedanta Advisor are **products built with SwarmKit**, not examples of it. They live under
`examples/` because that is where they started, and the cost of that has stopped being theoretical:
Minder is pinned to `swarmkit-runtime==1.55.0` against a framework at 1.193.0, and the Vedanta
workspace on `main` is a one-file stub while 92 commits of the real thing sit unmerged on a branch.
Neither of those is a packaging problem. They are what happens when a product has no repo of its own
to be released from.

This note is the plan for giving them one. It is the sibling of
[`extracting-the-pipeline.md`](extracting-the-pipeline.md) — same argument at a different layer:
SwarmKit should ship the framework and one honest reference, not carry applications.

## Goal

Minder and Vedanta each in their own repository, with their history, able to pin a released
`swarmkit-runtime` like any other consumer — and SwarmKit's tree carrying no application it is not
prepared to maintain.

## Non-goals

- **Not extracting `examples/sdlc-pipeline`.** It is the worked reference the docs are built on, it
  is the only example CI actually runs (`testpaths`), and it exists to demonstrate the framework
  rather than to be a product. It stays.
- **Not deciding what happens to `examples/sterling-oms`** (122 files) or `examples/rynko-content`.
  They are the same *shape* of question and deserve the same answer eventually, but bundling four
  extractions into one plan is how none of them happen.
- **Not changing either product.** No refactor, no restructure, no "while we're here". The extraction
  is a move; anything else is a separate change on the other side of it.
- **Not building a template or a cookiecutter.** Two repos is not a pattern yet.

## What is actually coupled today (measured, not assumed)

The good news, and the reason this is cheap:

| Coupling | Minder | Vedanta |
| --- | --- | --- |
| CI | none — `testpaths` excludes both | none |
| `uv` workspace membership | not a member | not a member |
| Runtime imports | `model_providers` only (public seam) | none |
| Removed pipeline surface | none | none |
| Branches touching the runtime | **zero**, across 17 branches | design branch touches 2 unrelated files |

The only two hooks in the SwarmKit tree are a `[tool.ruff.lint.per-file-ignores]` block for
`examples/minder/**` (11 relaxed rules) and eight docs cross-references, all of which are prose
mentions ("measured on the vedanta-advisor workspace", "fine for one box, e.g. Minder") rather than
links to files.

**Compatibility is verified, not hoped for.** On 1.193.0, `swarmkit validate examples/minder/workspace`
reports 9 topologies, 11 skills, 2 archetypes, 1 trigger — **no errors, 0 warnings, nothing
unreachable** — and every import Minder makes still resolves.

## The ordering decision, and it is the whole plan

**Bump Minder's pin BEFORE extracting it, not after.**

A bump from 1.55.0 to 1.193.0 is 138 releases in one step. Done here, a failure is diagnosed against
the runtime source in one command. Done in a fresh repo, on the same day the layout changed, every
failure has two candidate causes and the bisect has nowhere to go. The order is not a preference; it
is the difference between one variable and two.

So:

1. ~~**Bump and verify Minder in place.**~~ **Done** — 1.55.0 → 1.193.0, live. It was worth doing
   first for exactly the reason argued here: the bump pulled `mcp` 2.0.0 (`swarmkit-runtime`
   declares `mcp>=1.0`, so a breaking major is in range), 2.0 removed `mcp.server.fastmcp`, and all
   six of Minder's stdio servers died at import. Diagnosed against the runtime source in minutes.
   In a fresh repo on the day the layout changed, the same failure would have had two candidate
   causes and a bisect with nowhere to go.
2. ~~**Extract Minder** with history.~~ **Done.** `git subtree split` carried 87 commits and each
   branch's own history. The appliance now runs from `~/minder` on the same five volumes — the
   pinned compose project name is what made that a non-event rather than a restore.
3. **Extract Vedanta** from the design branch, not from `main`.
4. **Clean up** the SwarmKit tree in one PR.

## The live appliance is launched from the directory this plan deletes

`examples/minder` is not only source — it is the **compose working directory of a running
production stack** (four containers, `network_mode: host`, uptime measured in days, restart policy
`unless-stopped`). Two consequences the first draft of this note missed:

**The volume names are derived from the directory name, and that is a data-loss trap.** Compose
takes its project name from the folder (`minder`), and the project name prefixes the volumes:
`minder_minder-data`, `minder_ha-config`, `minder_frigate-media`. Clone the tree into a repo called
anything else — `minder-app`, `swarmkit-minder` — and `docker compose up` creates **new, empty**
volumes: the Home Assistant configuration and Minder's data are still on disk but nothing is looking
at them, which presents as a wiped appliance. The fix is one line and only works if you know to write
it: set `name: minder` explicitly at the top of `docker-compose.yml`, so the project name stops
depending on where the directory happens to sit. **Do that before the move, not after.**

**`network_mode: host` means the appliance shares the host's port space.** Anything testing a new
version on the same machine must bind a port the live stack does not use — Minder's serve is on
8321 — or it will fight the running system rather than run beside it.

## Preserving history

`git subtree split` — not a fresh `git init` and a copy.

```bash
git subtree split --prefix=examples/minder -b minder-extracted
# in a fresh clone of the new repo:
git pull ../swarmkit minder-extracted
```

Minder has **82 commits** touching its tree. Those are the record of why the alert bus is shaped the
way it is, why the VLM runs on CPU, and what the two-layer vision experiment concluded. A copy
throws all of it away and leaves a repo whose first commit is "initial import" — which is the state
this note exists to avoid repeating in a year.

The 17 Minder branches then rebase cleanly onto the new repo, because none of them touch anything
outside `examples/minder/`. That is worth doing branch by branch rather than in bulk: several are
4–8 weeks old and at least a few are probably dead. An extraction is a good moment to decide, and a
bad moment to preserve everything by default.

## Vedanta is a different job, and pretending otherwise loses the work

`main` carries **9 files**: one archetype and eight dataset pointers. The real workspace — 14 skills,
4 topologies, 5 archetypes, gates, scripts, sample outputs — is on `design/vedanta-advisor`, **92
commits** unmerged.

So the extraction source is **the branch**, not `main`. Extracting `main` would produce a repo
containing a stub and would strand the work in a branch on a repo Vedanta no longer lives in — the
worst outcome available.

Two further facts about that tree:

- **The eight `knowledge/datasets/*` entries are broken submodules.** They are gitlinks (mode
  `160000`) with **no `.gitmodules` file**, so they cannot be cloned, initialised, or updated by
  anyone. They have been reporting "modified content" in `git status` since they landed. They are not
  data; they are eight dangling pointers. **Delete them in the move** and record the upstream URLs in
  the new repo's README, where a reader can act on them.
- **`delivstat/vedanta-advisor` already exists** (private, created June 2026). The extraction target
  is that repo, and the first question is what is already in it — merging a 92-commit branch into a
  repo with independent history needs a decision (`--allow-unrelated-histories` and a merge, or a
  subtree under a subdirectory), not a guess.

## What SwarmKit keeps

Nothing of the products, and one honest pointer to each.

The docs mention both as evidence — "measured on the vedanta-advisor workspace", the dual-model
comparison, the serve-auth example. Those stay, with the paths updated to the new repos. Evidence
that a framework claim was measured against a real product is worth more than the code being
in-tree, and it is the only thing the SwarmKit reader loses.

Removed in the cleanup PR:

- `examples/minder/`, `examples/vedanta-advisor/`
- the `examples/minder/**` ruff carve-out in `pyproject.toml` — 11 relaxed rules that would otherwise
  silently apply to nothing while telling a future reader that example code still lives here
- any `just` target or doc link pointing into either tree (there are none today; assert it rather
  than assume it, since the pipeline removal left six broken `just` targets behind exactly this way)

## What the new repos need on day one

Not a framework. The minimum that makes each releasable:

- a `README.md` that says what it is, what it needs, and how to run it
- the pinned `swarmkit-runtime` version, in one place
- its own CI running its own tests (Minder has ~6 test files that nothing currently runs)
- its own `design/` — Minder already has **18 design notes** under `examples/minder/design/`, which
  move with it and immediately look correct rather than orphaned

Minder additionally carries `docker-compose.yml`, a Dockerfile, six MCP servers and a webapp — it is
already shaped like a repo. That is the strongest evidence it should be one.

## What the bump already taught this plan

- **`mcp>=1.0` in the runtime lets a breaking major in.** The framework survives it (there is a
  compat shim in `mcp/_sdk_compat.py`); its *consumers* do not, and they find out at import time in
  production. Worth deciding separately whether the runtime should pin `mcp<3` — noted here because
  the extraction is what made a consumer's exposure visible.
- **`/health` is not evidence the product works.** After the bump serve answered ok, registered all
  9 topologies as MCP tools and started the trigger scheduler, while every camera event failed —
  none of those checks touch a stdio server. Any post-extraction smoke test has to exercise the
  event path, not the port.
- **Minder's own tests ran nowhere.** It was worse than the six files this note first counted: **26 test modules, 139 tests**, of which 22 modules could not even be COLLECTED (they import siblings directly, having been written to run one at a time from their own folder). The first run as a suite found **7 genuine failures**. None of that was visible while the tree sat in a repo whose `testpaths` excluded `examples/`. The new repo gets CI on day one, and
  that is a gain from extraction rather than a cost of it.

## Test plan

The extraction is a move, so the test is that nothing changed:

- **Before:** record `swarmkit validate` output for the Minder workspace and the Vedanta branch
  workspace. **After:** identical output from the new repos against the same runtime version.
- Minder's own tests (`test_alert_bus.py`, `test_channels.py`, `test_bot_resilience.py`,
  `mcp-servers/frigate/test_escalate.py`, …) run **green in the new repo** — they run nowhere today,
  so this is the first time they are a gate rather than a file.
- `docker compose build` succeeds in the new repo at the bumped pin.
- In SwarmKit after cleanup: full suite green, `mkdocs build --strict` green (it catches a doc link
  into a deleted tree, which is how the pipeline removal's four broken links were found), and
  `git grep -i "examples/minder\|examples/vedanta"` returns nothing outside the changelog.

## Demo plan

Minder running from its own repo against a released `swarmkit-runtime` from PyPI — the stack up,
a router request answered, an alert delivered — with no path into a SwarmKit checkout anywhere in
the compose file. That is the proof the seam is real rather than claimed, in the same shape as
`examples/pipeline-orchestrator/` importing no runtime module.

## Open questions

1. **Public or private?** Minder is a product with a business behind it; SwarmKit is open source.
   Extracting it *to a private repo* is a decision about the product, not about layering, and it
   should be made deliberately rather than inherited from where the code happens to sit today.
2. **What is already in `delivstat/vedanta-advisor`?** The merge strategy for 92 commits of unrelated
   history depends entirely on the answer, and this note cannot see inside it.
3. **Do the Minder branches survive the move?** 17 branches, ≤3 commits each, 4–8 weeks old. Carrying
   all of them costs nothing mechanically and preserves 17 open decisions nobody has revisited.
4. **Does SwarmKit want a `swarmkit-examples` repo** for `sterling-oms` and `rynko-content` too, or
   do they stay? Deliberately deferred — see non-goals — but the answer probably follows whatever
   these two extractions teach.
