#!/usr/bin/env python
"""Ask each MCP-backed skill's server whether its tool still exists.

    uv run python scripts/check_skill_liveness.py [workspace] [--json]

A curated skill list that nobody re-checks becomes an awesome-list, and those rot in months — the
server renames a tool, changes an argument, adds auth, or disappears, and the entry keeps claiming
it works. SwarmKit can do what a list structurally cannot: **start the server and ask.**

That is the difference between "pre-validated" and "verified 2026-09-02 against 11 live tools" — a
claim with a date rather than a promise. See `design/details/skill-catalogue.md`.

Three states, and the third is the point:

* **verified** — the server started and the tool it names is present.
* **broken** — the server started and the tool is gone, or the server would not start at all.
* **unverifiable** — the server needs a credential this environment does not have. Reported
  honestly rather than passed silently, because the most-wanted entries are exactly the ones
  public CI cannot check, and a green badge that means "we did not look" is worse than no badge.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "runtime" / "src"))

#: How long one server gets to start and answer. Generous: a first run may fetch a package.
LIST_TIMEOUT_S = float(os.environ.get("SWARMKIT_LIVENESS_TIMEOUT", "120"))

_ENV_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass
class Result:
    skill_id: str
    server_id: str
    tool: str
    state: str  # verified | broken | unverifiable
    detail: str = ""

    @property
    def mark(self) -> str:
        return {"verified": "✓", "broken": "✗", "unverifiable": "-"}[self.state]


def _needs_credentials(cfg: Any) -> str:
    """Why this server cannot be checked here, or "" if it can.

    A `credentials_ref` says so outright. An `env` value interpolating a variable that is not set
    says the same thing less directly — and running the server anyway would either fail for the
    wrong reason or, worse, succeed against someone's real account.
    """
    if getattr(cfg, "credentials_ref", ""):
        return f"needs credential '{cfg.credentials_ref}'"
    for key, raw in (getattr(cfg, "env", None) or {}).items():
        for var in _ENV_VAR.findall(str(raw)):
            if not os.environ.get(var):
                return f"needs ${var} (for {key})"
    return ""


async def _tools_for(cfg: Any, server_id: str) -> tuple[set[str], str]:
    """Every tool the server lists, or an empty set and the reason it could not be asked."""
    from swarmkit_runtime.mcp._client import MCPClientManager  # noqa: PLC0415

    manager = MCPClientManager({server_id: cfg}, workspace_root=None)
    try:
        listed = await asyncio.wait_for(manager.list_tools(server_id), timeout=LIST_TIMEOUT_S)
        return {t["name"] for t in listed}, ""
    except TimeoutError:
        return set(), f"did not answer within {LIST_TIMEOUT_S:.0f}s"
    except Exception as exc:
        return set(), f"{type(exc).__name__}: {exc}"
    finally:
        # Teardown must never mask the finding: a server that answered and then failed to close
        # cleanly is still a server whose tool exists.
        with contextlib.suppress(Exception):
            await manager.close_all()


async def check(workspace: Path) -> list[Result]:
    from swarmkit_runtime.mcp._client import parse_mcp_servers  # noqa: PLC0415
    from swarmkit_runtime.resolver import resolve_workspace  # noqa: PLC0415
    from swarmkit_runtime.skills import impl_get  # noqa: PLC0415

    ws = resolve_workspace(workspace)
    servers = parse_mcp_servers(getattr(ws.raw, "mcp_servers", None))

    wanted: dict[str, list[tuple[str, str]]] = {}
    for skill_id, skill in sorted(ws.skills.items()):
        impl = skill.raw.implementation
        if impl_get(impl, "type") != "mcp_tool":
            continue
        wanted.setdefault(str(impl_get(impl, "server")), []).append(
            (skill_id, str(impl_get(impl, "tool")))
        )

    results: list[Result] = []
    for server_id, entries in sorted(wanted.items()):
        cfg = servers.get(server_id)
        if cfg is None:
            results.extend(
                Result(s, server_id, t, "broken", "the workspace declares no such server")
                for s, t in entries
            )
            continue

        why = _needs_credentials(cfg)
        if why:
            results.extend(Result(s, server_id, t, "unverifiable", why) for s, t in entries)
            continue

        tools, failure = await _tools_for(cfg, server_id)
        if failure:
            results.extend(
                Result(s, server_id, t, "broken", f"server did not start — {failure}")
                for s, t in entries
            )
            continue
        for skill_id, tool in entries:
            if tool in tools:
                results.append(Result(skill_id, server_id, tool, "verified"))
            else:
                near = ", ".join(sorted(tools)[:4])
                results.append(
                    Result(
                        skill_id,
                        server_id,
                        tool,
                        "broken",
                        f"tool not listed; server offers: {near}…",
                    )
                )
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("workspace", nargs="?", default=str(REPO / "reference"), type=Path)
    ap.add_argument("--json", action="store_true", help="machine-readable, for a catalogue badge")
    args = ap.parse_args()

    results = asyncio.run(check(args.workspace))
    checked_at = datetime.now(UTC).strftime("%Y-%m-%d")

    if args.json:
        print(
            json.dumps(
                {
                    "checked_at": checked_at,
                    "workspace": str(args.workspace),
                    "results": [r.__dict__ for r in results],
                },
                indent=2,
            )
        )
    else:
        print(f"skill liveness — {args.workspace}, {checked_at}\n")
        for r in results:
            line = f"  {r.mark} {r.skill_id:24} {r.server_id}/{r.tool}"
            print(line if r.state == "verified" else f"{line}  — {r.detail}")
        counts = {
            s: sum(1 for r in results if r.state == s)
            for s in ("verified", "broken", "unverifiable")
        }
        print(
            f"\n{counts['verified']} verified · {counts['broken']} broken · "
            f"{counts['unverifiable']} unverifiable"
        )

    # Only `broken` fails. `unverifiable` is an honest state, not a defect — failing on it would
    # make every credentialed entry permanently red and teach everyone to ignore the check.
    return 1 if any(r.state == "broken" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
