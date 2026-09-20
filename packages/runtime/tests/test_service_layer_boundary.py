"""Every interface starts work through the same service.

CLI, `POST /run`, chat, MCP (both front doors), A2A, webhooks, triggers and workers are supposed to
reach the runtime the same way. Nothing said so and nothing enforced it, and it drifted five times:
the `/mcp/` tool path, `swarmkit mcp-serve`, chat, `swarmkit run` and its `--resume`. In one case
the fixed path and the broken one sat in the *same file*.

The failure mode is what makes a guard worth having: a second path into the runtime is a second set
of rules to keep in step, and it drifts in silence. There is no error and no warning — the only
symptom is a run behaving differently depending on which door it came through. `/mcp/` had no
capacity gate for months while every other caller was bounded, and nothing anywhere said so.

Modelled on `test_nothing_outside_persistence_hardcodes_a_sqlite_path`, which exists because that
rule "regressed three times". Same shape: an allowlist with a reason per entry, and a
`# noqa: service-layer` escape hatch for a call site that is deliberately different.

See docs/notes/service-layer-discipline.md.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src/swarmkit_runtime"

#: Modules that may call `WorkspaceRuntime.run` directly, and why. Anything else must go through
#: `JobService` — which is what gives a run its durable row, canary routing, capacity gate,
#: input-schema precheck and `source`.
_ALLOWED: dict[str, str] = {
    # The sanctioned call site: this IS the executor JobService starts.
    "server/_jobs.py": "execute_job — the one place a job's run is actually performed",
    # Defines the method.
    "_workspace_runtime.py": "defines run()",
}

#: `<something>.run(` where the something looks like a workspace runtime.
_RUN_CALL = re.compile(r"\b(rt|runtime|_runtime|self\._runtime)\.run\s*\(")


def test_no_interface_reaches_past_the_service_to_run_a_topology() -> None:
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        if rel in _ALLOWED:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for num, line in enumerate(lines, 1):
            if not _RUN_CALL.search(line):
                continue
            # The escape hatch, on the line or either of the two above it — these call sites are
            # multi-line, so the marker goes where a reader will see it.
            context = "".join(lines[max(0, num - 3) : num])
            if "noqa: service-layer" in context:
                continue
            offenders.append(f"{rel}:{num}: {line.strip()}")

    assert not offenders, (
        "these start a run without going through JobService, so they get no durable row, no "
        "canary routing and no capacity gate. Route them through the service, or mark the call "
        "`# noqa: service-layer` with the reason it is deliberately different:\n"
        + "\n".join(offenders)
    )


def test_the_allowlist_still_describes_real_files() -> None:
    """An allowlist entry for a file that moved is an exemption nobody is checking."""
    missing = [rel for rel in _ALLOWED if not (_SRC / rel).is_file()]
    assert not missing, f"allowlisted files that no longer exist: {missing}"
