"""Package publisher — bundle a workspace for distribution.

Creates a .tar.gz of the workspace directory suitable for
`swarmkit install`. Excludes runtime state, caches, and secrets.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

from rich.console import Console

console = Console()

_EXCLUDE_PATTERNS = {
    "__pycache__",
    "*.pyc",
    ".git",
    ".swarmkit",
    "node_modules",
    ".env",
    ".env.*",
    "*.sqlite",
    "*.db",
    "dist",
}


def _should_exclude(path: str) -> bool:
    parts = Path(path).parts
    for pattern in _EXCLUDE_PATTERNS:
        if pattern.startswith("*"):
            suffix = pattern[1:]
            if any(p.endswith(suffix) for p in parts):
                return True
        elif pattern.endswith(".*"):
            prefix = pattern[:-2]
            if any(p.startswith(prefix) for p in parts):
                return True
        elif any(p == pattern for p in parts):
            return True
    return False


def publish_package(workspace_path: Path, output_dir: Path) -> None:
    """Bundle a workspace into a distributable .tar.gz."""
    if not (workspace_path / "workspace.yaml").exists():
        console.print(f"[red]No workspace.yaml in {workspace_path}[/red]")
        raise SystemExit(1)

    pkg_yaml = workspace_path / "package.yaml"
    if pkg_yaml.exists():
        import yaml  # noqa: PLC0415

        pkg = yaml.safe_load(pkg_yaml.read_text(encoding="utf-8"))
        name = pkg.get("name", workspace_path.name).replace("@", "").replace("/", "-")
        version = pkg.get("version", "0.0.0")
    else:
        name = workspace_path.name
        version = "0.0.0"
        console.print("[yellow]No package.yaml found. Using workspace directory name.[/yellow]")

    output_dir.mkdir(parents=True, exist_ok=True)
    tarball = output_dir / f"{name}-{version}.tar.gz"

    file_count = 0
    with tarfile.open(tarball, "w:gz") as tar:
        for path in sorted(workspace_path.rglob("*")):
            rel = path.relative_to(workspace_path)
            if _should_exclude(str(rel)):
                continue
            if path.is_file():
                tar.add(path, arcname=f"{name}/{rel}")
                file_count += 1

    size_kb = tarball.stat().st_size / 1024
    console.print(
        f"[green]Package created: {tarball}[/green]\n  {file_count} files, {size_kb:.0f} KB"
    )
    console.print(f"\n[dim]Install with: swarmkit install {tarball}[/dim]")
