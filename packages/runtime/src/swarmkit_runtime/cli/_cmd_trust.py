"""CLI — ``swarmkit trust``: surface, apply, and clear trust-accrual changesets (§6.2.3).

When a relayed capability is approved by an operator N times with no denial, the runtime proposes
adding it to the archetype's allowlist (``executor.config.allowed_tools``). ``trust list`` shows
those proposals; ``trust apply`` performs the allowlist edit (a human action — the runtime never
widens a grant on its own); ``trust clear`` lifts a denial block so a pair can accrue again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from swarmkit_runtime.trust import TrustStore

from ._app import trust_app
from ._common import _stderr


@trust_app.command("list")
def trust_list(
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
) -> None:
    """List pending allowlist-changeset proposals (archetype ← capability + the approval count)."""
    proposals = TrustStore(workspace_path.resolve()).proposals()
    if not proposals:
        typer.echo("No trust proposals. Capabilities accrue as operators approve relayed requests.")
        return
    for p in proposals:
        typer.echo(f"  {p.archetype:<20} {p.capability:<28} ({p.approvals} approvals)")
    typer.echo("\nApply one with: swarmkit trust apply <archetype> <capability>")


@trust_app.command("apply")
def trust_apply(
    archetype: str,
    capability: str,
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
) -> None:
    """Apply a proposal: add the capability to the archetype's ``executor.config.allowed_tools`` and
    mark the proposal applied. This is the human-recorded grant — future runs stop asking."""
    root = workspace_path.resolve()
    store = TrustStore(root)
    if not any(p.archetype == archetype and p.capability == capability for p in store.proposals()):
        _stderr(f"No pending proposal for {archetype!r} + {capability!r}.")
        raise typer.Exit(1)

    path = _archetype_path(root, archetype)
    if path is None:
        _stderr(f"Archetype {archetype!r} not found under {root / 'archetypes'}.")
        raise typer.Exit(1)

    added = _add_to_allowlist(path, capability)
    store.apply(archetype, capability)
    if added:
        typer.echo(f"✓ Added {capability!r} to {archetype}'s allowlist ({path.name}).")
    else:
        typer.echo(f"✓ {capability!r} already in {archetype}'s allowlist; marked applied.")


@trust_app.command("clear")
def trust_clear(
    archetype: str,
    capability: str,
    workspace_path: Annotated[
        Path, typer.Argument(help="Workspace root.", show_default=False)
    ] = Path("."),
) -> None:
    """Lift a denial block and reset a pair's tally so it can accrue toward a proposal again."""
    if TrustStore(workspace_path.resolve()).clear(archetype, capability):
        typer.echo(f"✓ Cleared the block on {archetype!r} + {capability!r}.")
        return
    _stderr(f"No trust record for {archetype!r} + {capability!r}.")
    raise typer.Exit(1)


def _archetype_path(root: Path, archetype: str) -> Path | None:
    """Locate the archetype's YAML by its ``metadata.id`` (filename is not authoritative)."""
    directory = root / "archetypes"
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.y*ml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if isinstance(data, dict) and (data.get("metadata") or {}).get("id") == archetype:
            return path
    return None


def _add_to_allowlist(path: Path, capability: str) -> bool:
    """Append ``capability`` to the archetype's ``executor.config.allowed_tools`` (a comma-separated
    string). Returns ``False`` when it was already present. Preserves the rest of the document."""
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    executor = data.setdefault("executor", {})
    config = executor.setdefault("config", {})
    existing = str(config.get("allowed_tools", "")).strip()
    tools = [t.strip() for t in existing.split(",") if t.strip()]
    if capability in tools:
        return False
    tools.append(capability)
    config["allowed_tools"] = ", ".join(tools)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return True
