# CLI unimplemented-subcommand UX

## Problem

Every subcommand listed in `swael --help` that isn't wired yet raises a
bare `NotImplementedError`. Typer lets the traceback through to stderr, so
a reader who tries `swael init` today sees:

```
╭───────────────────── Traceback (most recent call last) ──────────────────────╮
│ /.../swael_runtime/cli/__init__.py:207 in init                            │
│ ...                                                                           │
╰──────────────────────────────────────────────────────────────────────────────╯
```

That reads as *the CLI is broken*, not *this feature lands later*. It's the
first impression a new user gets if they explore `--help` before reading
the roadmap.

## Goal

Replace the traceback with a one-line, honest message:

```
$ swael init
swael init: not yet implemented — planned for M8 (Workspace Authoring Swarm).
See design/IMPLEMENTATION-PLAN.md for the roadmap.
$ echo $?
2
```

Exit with the existing usage-error code (2). A not-implemented command is
functionally "not usable as typed"; the existing code already covers that
semantic slot — no need to introduce a new one for callers to learn.

## Non-goals

- **Reorganising the CLI.** Which commands exist, what they're named, and
  their final signatures are design §14.2 concerns. This note only
  changes the failure UX.
- **Hiding unimplemented commands from `--help`.** They stay listed so
  readers can see the planned shape. The message makes the status clear.

## Implementation

A single helper in `cli/__init__.py`:

```python
def _not_implemented(command: str, *, milestone: str) -> None:
    typer.echo(
        f"swael {command}: not yet implemented — planned for {milestone}. "
        "See design/IMPLEMENTATION-PLAN.md for the roadmap.",
        err=True,
    )
    raise typer.Exit(_EXIT_USAGE)
```

Every stubbed subcommand body becomes a one-liner:

```python
@app.command()
def init() -> None:
    """Launch the Workspace Authoring Swarm in terminal chat mode (design §14.2)."""
    _not_implemented("init", milestone="M8 (Workspace Authoring Swarm)")
```

Milestone labels are informational — they read in the user-visible
message, so they should match the plan. A comment above the stub block
reminds future editors to keep them honest.

## Test plan

`packages/runtime/tests/test_cli_stubs.py`. Parametrised over each
stubbed subcommand:

- Exit code is `2`.
- Stderr contains `not yet implemented` and the command name.
- Stdout is empty.
- No `Traceback` leaks anywhere.

As each subcommand gets implemented in its own milestone, its test case
is removed from the parametrised list (and a real integration test takes
its place). This means the test file stays honest: only genuinely-stubbed
commands are asserted here.
