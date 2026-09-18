---
title: swarmkit upgrade — upgrade in place, with a breaking-change gate
description: A CLI that detects how the runtime was installed and with which extras, checks PyPI for a newer version, shows any breaking changes between the two, and asks before it runs the install.
tags: [cli, release, operations]
status: accepted
---

# swarmkit upgrade

## The gap

Upgrading is a manual `uv tool install --upgrade "swarmkit-runtime[…]"` — and the operator has to
remember which extras they have and, worse, has no way to see that a version between theirs and the
newest is breaking. The changelog has 240+ entries and (until now) zero breaking markers, so an
operator crossing 1.189.0 (pipeline removed) or 1.199.0 (`readonly` needs declared `effects`) finds
out at runtime.

## Goal

`swarmkit upgrade` that: detects the install method and extras, finds the newest version (or a
`--to`), lists any breaking changes in between, and **asks before executing** — always, when a
breaking change is crossed. `--check` reports without acting (CI-friendly). It refuses, with the
exact command, for installs it does not own (Docker, an unrecognised layout) rather than guessing.

## Non-goals

- **Not the control plane.** Upgrading fleet instances is a control-plane concern (a deploy over
  the connector), and the runtime never depends on that package. `swarmkit upgrade` upgrades the
  local install only.
- **Not unattended/auto.** It runs when a person types it. A serve that upgrades itself mid-flight
  is a restart nobody asked for. `--check` covers "am I behind" without acting.
- **Not schema pinning.** `swarmkit-schema` is bundled, not user-installed.

## Design

### Breaking changes are a curated data file

Tag subjects are immutable history, so breaking-ness cannot be back-filled into the changelog.
Instead a bundled data module `swarmkit_runtime/_breaking_changes.py` (ships in the wheel, read
offline) lists them:

```python
BREAKING_CHANGES = [
    BreakingChange("1.189.0", "The bundled pipeline layer was removed (StageGraph, the saga "
                   "controller, `swarmkit orchestrator`/`pipeline`, `POST /pipelines/*`). "
                   "Sequencing is the application's.", "design-notes/extracting-the-pipeline/"),
    BreakingChange("1.199.0", "`permission: readonly` now decides by declared `effects`, not by "
                   "scanning the tool name; an unknown effect under readonly is denied.",
                   "notes/mcp-effects-migration/... "),
]
```

Each is `{version, summary, migration}` (migration is a docs slug). New breaking releases add an
entry here in the same PR, and the tag convention going forward includes `BREAKING` / a `!` scope so
the two never drift. A test asserts every entry's version parses and is ≤ the current runtime.

### What it does

```
swarmkit upgrade [--check] [--yes] [--to X.Y.Z]
```

1. **Installed** = `runtime_version()`.
2. **Target** = `--to`, else the newest non-yanked `swarmkit-runtime` on PyPI
   (`GET https://pypi.org/pypi/swarmkit-runtime/json`, short timeout). Offline without `--to` is a
   clean error; offline *with* `--to` still works for the plan (the breaking file is local) but the
   install step needs the network.
3. **Extras** = reconstructed from what is importable: `swarmkit_webui` → `ui`, `psycopg` →
   `postgres`, the vendor SDKs → `anthropic`/`openai`/`google`. So the upgrade re-installs the same
   surface, not a bare package.
4. **Install method** from `sys.executable` / the package location: `uv tool`
   (`…/uv/tools/…`), `pipx` (`…/pipx/…`), or a venv `pip`. Docker (`/.dockerenv`) or an
   unrecognised layout → refuse to run, print the exact command.
5. **Breaking changes** = entries in `(installed, target]`. Printed with their migration links.
6. **Plan** printed: `1.240.0 → 1.245.0, keeps [ui, postgres]`, then the breaking list.
7. **Execute**: run the method's command (`uv tool install --upgrade "swarmkit-runtime[ui,postgres]"`,
   `pipx upgrade`, `pip install -U`). Unless `--yes`: confirm — and when any breaking change is in
   range the prompt is mandatory and names them, never a bare y/N.
8. `--check`: print the plan (and breaking changes) and exit — `1` if behind, `0` if current.
   Never executes, never prompts.

### Refusal, not a guess

For Docker / unknown installs it prints, e.g., `This looks like a container image; upgrade by
pulling a newer tag:  docker pull …` or `Reinstall with:  pip install -U "swarmkit-runtime[ui]"`,
and exits non-zero without running anything. A manager that shells the wrong installer into a venv
it does not own is how people end up with two runtimes.

## Test plan

- `_breaking_changes`: every entry parses; `changes_between(a, b)` returns the right slice
  (exclusive low, inclusive high); the current runtime is ≥ the newest listed.
- Detection: `sys.executable` under `uv/tools`, `pipx`, a venv → the right method; `/.dockerenv` →
  refuse.
- Extras: a fake environment where `swarmkit_webui`/`psycopg` import (or not) → the right extras.
- PyPI: a stubbed `GET /pypi/...json` → target resolves; a yanked newest is skipped; offline →
  clean error.
- The command: `--check` exits 1 when behind and 0 when current, never runs a subprocess (assert
  the runner is not called); a breaking change in range forces the prompt (a `no` aborts, `--yes`
  proceeds); the executed argv matches the method + extras; a Docker env refuses with the command.

## Demo plan

`swarmkit upgrade --check` on a deliberately old `--to`-simulated install, showing the plan with a
breaking change and its migration link; then `--yes` running the detected command (captured, not a
real network install, in the demo).
