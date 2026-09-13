#!/usr/bin/env python
"""Generate the parts of the reference docs that are inventories, from the code they inventory.

    uv run python scripts/gen_reference.py            # rewrite the generated sections
    uv run python scripts/gen_reference.py --check    # exit 1 if they would change (CI)

Three surfaces rot fastest because they are lists that grow with every release and are maintained
nowhere near the code that grows them:

* **CLI commands** — every ``swarmkit`` command and its help line, walked from the Typer app.
  ``docs/site/reference/cli.md`` documented 20 of 61.
* **Environment variables** — from ``swarmkit_runtime._env_registry.REGISTRY``, which is what
  ``/system`` reports and a test holds equal to what the code reads. ``cli.md`` documented 12 of 46
  and gave ``SWARMKIT_MAX_TOOL_TURNS`` a default that had been wrong for months.
* **HTTP endpoints** — every route on ``swarmkit serve``, from the app's OpenAPI document, with the
  first line of the handler's docstring. ``serve.md`` documented 25 of 93 operations.

Each lands between ``<!-- BEGIN GENERATED: name -->`` / ``<!-- END GENERATED: name -->`` markers;
prose outside the markers is hand-written and untouched. ``scripts/check_docs.py`` runs ``--check``,
so a route added without regenerating fails the docs check the same way a schema edit without
codegen fails the drift check.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from swarmkit_runtime._env_registry import REGISTRY, SECRET, URLISH
from swarmkit_runtime.cli import app
from swarmkit_runtime.server._app import create_app

# Importing the runtime here is the point: the docs are generated from the objects, not from
# grep. The imports are slow (the CLI pulls in most of the package), which is fine for a script.
from typer.main import get_command

REPO = Path(__file__).resolve().parents[1]


CLI_MD = REPO / "docs/site/reference/cli.md"
HTTP_MD = REPO / "docs/site/reference/http-api.md"
WORKSPACE = REPO / "examples/hello-swarm/workspace"

MARK = "<!-- BEGIN GENERATED: {name} -->\n{body}\n<!-- END GENERATED: {name} -->"


def _cell(text: str) -> str:
    """A value safe inside a Markdown table cell."""
    return text.replace("|", "\\|")


def _replace(text: str, name: str, body: str) -> str:
    pat = re.compile(
        rf"<!-- BEGIN GENERATED: {name} -->\n(?:.*?\n)?<!-- END GENERATED: {name} -->", re.S
    )
    block = MARK.format(name=name, body=body.rstrip("\n"))
    if not pat.search(text):
        raise SystemExit(f"no GENERATED: {name} markers in the target file")
    return pat.sub(lambda _m: block, text)


# ---- CLI --------------------------------------------------------------------------------------


def cli_table() -> str:
    rows: list[tuple[str, str]] = []

    def walk(cmd: object, prefix: str) -> None:
        subs = getattr(cmd, "commands", None)
        if subs:
            for name, sub in sorted(subs.items()):
                walk(sub, f"{prefix} {name}")
            return
        help_text = (getattr(cmd, "help", "") or "").strip().split("\n")[0].strip()
        rows.append((prefix, help_text))

    walk(get_command(app), "swarmkit")
    out = [
        f"{len(rows)} commands, from the CLI itself (`swarmkit <command> --help` for the options).",
        "",
        "| Command | What it does |",
        "|---|---|",
    ]
    for cmd, help_text in rows:
        out.append(f"| `{cmd}` | {_cell(help_text)} |")
    return "\n".join(out)


# ---- environment ------------------------------------------------------------------------------


def env_table() -> str:
    groups: dict[str, list[tuple[str, str, str]]] = {}
    for e in REGISTRY:
        groups.setdefault(e.group, []).append((e.name, e.description, e.kind))
    out = [
        f"{len(REGISTRY)} variables, from the runtime's own registry (`swarmkit system` and "
        "`GET /system` report the same list, secrets masked). A variable the code reads and the "
        "registry does not know fails a test.",
    ]
    for group, rows in groups.items():
        out += ["", f"**{group}**", "", "| Variable | Purpose |", "|---|---|"]
        for name, desc, kind in rows:
            tag = (
                " *(secret)*"
                if kind == SECRET
                else " *(URL; userinfo masked)*"
                if kind == URLISH
                else ""
            )
            out.append(f"| `{name}` | {_cell(desc)}{tag} |")
    return "\n".join(out)


# ---- HTTP -------------------------------------------------------------------------------------


def _section(path: str) -> str:
    head = path.strip("/").split("/")[0]
    return {
        "api": "Portal API (`/api/*` — what the web portal calls)",
        "run": "Runs and jobs",
        "jobs": "Runs and jobs",
        "hooks": "Runs and jobs",
        "events": "Events",
        "gates": "Review and gates",
        "review": "Review and gates",
        "artifacts": "Artifacts",
        "conversations": "Conversations",
        "memory": "Governed memory",
        "fleet": "Fleet",
        "canary": "Canary deployments",
        "auth": "Authentication",
        "auth-info": "Authentication",
        "whoami": "Authentication",
        "mcp": "MCP",
    }.get(head, "Workspace and introspection")


def http_page() -> str:
    logging.disable(logging.CRITICAL)
    spec = create_app(WORKSPACE).openapi()
    order = [
        "Runs and jobs",
        "Events",
        "Review and gates",
        "Artifacts",
        "Conversations",
        "Governed memory",
        "Workspace and introspection",
        "Canary deployments",
        "Authentication",
        "Fleet",
        "MCP",
        "Portal API (`/api/*` — what the web portal calls)",
    ]
    by: dict[str, list[tuple[str, str, str]]] = {k: [] for k in order}
    n = 0
    for path, ops in sorted(spec["paths"].items()):
        if path == "/api/{rest}":
            continue  # the portal's catch-all 404, not an endpoint
        for method, op in ops.items():
            desc = (op.get("description") or "").strip().split("\n")[0].strip()
            by[_section(path)].append((method.upper(), path, desc))
            n += 1
    out = [
        "# HTTP API",
        "",
        "Every endpoint `swarmkit serve` exposes, generated from the server's own OpenAPI document "
        "(`GET /openapi.json` on a running instance has the schemas; `/docs` renders them). "
        f"{n} operations. The prose reference — auth modes, triggers, attachments, SSE — is "
        "[Serve mode](serve.md); the event contract an application consumes is "
        "[Events](events.md).",
        "",
        "Paths are relative to the server root. `{...}` segments are path parameters.",
        "",
    ]
    for sec in order:
        rows = by[sec]
        if not rows:
            continue
        out += [f"## {sec}", "", "| Method | Path | What it does |", "|---|---|---|"]
        for m, p, d in rows:
            out.append(f"| `{m}` | `{p}` | {_cell(d)} |")
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


# ---- main -------------------------------------------------------------------------------------


def render() -> dict[Path, str]:
    cli = CLI_MD.read_text(encoding="utf-8")
    cli = _replace(cli, "commands", cli_table())
    cli = _replace(cli, "env", env_table())
    return {CLI_MD: cli, HTTP_MD: http_page()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    want = render()
    drift = [
        p for p, text in want.items() if not p.exists() or p.read_text(encoding="utf-8") != text
    ]
    if a.check:
        for p in drift:
            print(f"  {p.relative_to(REPO)}: stale — run scripts/gen_reference.py")
        return 1 if drift else 0
    for p, text in want.items():
        p.write_text(text, encoding="utf-8")
        print(f"wrote {p.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
