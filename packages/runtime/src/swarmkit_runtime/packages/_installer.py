"""Package installer — install and manage SwarmKit expertise packages.

Packages are tarballs containing a workspace directory with a package.yaml.
They can be installed from:
  - Local path (directory or .tar.gz)
  - GitHub release URL
  - Package name (looks up in registry — future)

Installed packages live in ~/.swarmkit/packages/<name>/.
"""

from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

_PACKAGES_DIR = Path.home() / ".swarmkit" / "packages"

console = Console()


def _packages_dir() -> Path:
    _PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
    return _PACKAGES_DIR


def _load_package_yaml(path: Path) -> dict[str, Any]:
    """Load package.yaml from a workspace directory."""
    import yaml  # noqa: PLC0415

    pkg_file = path / "package.yaml"
    if not pkg_file.exists():
        raise FileNotFoundError(f"No package.yaml in {path}")
    result: dict[str, Any] = yaml.safe_load(pkg_file.read_text(encoding="utf-8"))
    return result


def install_package(source: str, *, upgrade: bool = False) -> None:
    """Install a package from a local path or URL."""
    source_path = Path(source)

    if source_path.is_dir():
        _install_from_dir(source_path, upgrade=upgrade)
    elif source_path.exists() and source_path.suffix in (".gz", ".tgz"):
        _install_from_tarball(source_path, upgrade=upgrade)
    elif source.startswith("https://"):
        _install_from_url(source, upgrade=upgrade)
    else:
        console.print(
            f"[red]Cannot resolve package: {source}[/red]\n"
            "Provide a local directory, .tar.gz file, or https:// URL."
        )
        raise SystemExit(1)


def _install_from_dir(source: Path, *, upgrade: bool) -> None:
    """Install from a local workspace directory."""
    source = source.resolve()
    if not (source / "workspace.yaml").exists():
        console.print(f"[red]No workspace.yaml in {source}[/red]")
        raise SystemExit(1)

    pkg_yaml = source / "package.yaml"
    if pkg_yaml.exists():
        pkg = _load_package_yaml(source)
        name = pkg.get("name", source.name)
    else:
        name = source.name

    name_safe = name.replace("@", "").replace("/", "_")
    dest = _packages_dir() / name_safe

    if dest.exists():
        if not upgrade:
            console.print(
                f"[yellow]Package {name} already installed. Use --upgrade to replace.[/yellow]"
            )
            return
        shutil.rmtree(dest)

    shutil.copytree(
        source,
        dest,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
            ".git",
            "node_modules",
            ".swarmkit",
        ),
    )

    _write_manifest(dest, name, source)
    console.print(f"[green]Installed {name} → {dest}[/green]")


def _install_from_tarball(source: Path, *, upgrade: bool) -> None:
    """Install from a .tar.gz file."""
    import tempfile  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as tmpdir:
        with tarfile.open(source, "r:gz") as tar:
            tar.extractall(tmpdir)

        extracted = Path(tmpdir)
        dirs = [d for d in extracted.iterdir() if d.is_dir()]
        workspace_dir = dirs[0] if len(dirs) == 1 else extracted

        if not (workspace_dir / "workspace.yaml").exists():
            for d in dirs:
                if (d / "workspace.yaml").exists():
                    workspace_dir = d
                    break

        _install_from_dir(workspace_dir, upgrade=upgrade)


def _install_from_url(url: str, *, upgrade: bool) -> None:
    """Install from a URL (GitHub release tarball)."""
    import tempfile  # noqa: PLC0415

    import httpx  # noqa: PLC0415

    console.print(f"Downloading {url}...")
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as f:
        resp = httpx.get(url, follow_redirects=True)
        resp.raise_for_status()
        f.write(resp.content)
        tmp_path = Path(f.name)

    try:
        _install_from_tarball(tmp_path, upgrade=upgrade)
    finally:
        tmp_path.unlink(missing_ok=True)


def _write_manifest(dest: Path, name: str, source: Path) -> None:
    """Write install manifest for tracking."""
    manifest = {
        "name": name,
        "source": str(source),
        "installed_at": __import__("datetime").datetime.now().isoformat(),
    }
    (dest / ".swarmkit_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def list_packages(workspace_path: Path) -> None:
    """List installed packages."""
    pkg_dir = _packages_dir()

    installed = []
    for d in sorted(pkg_dir.iterdir()):
        if not d.is_dir():
            continue
        manifest_file = d / ".swarmkit_manifest.json"
        if manifest_file.exists():
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        else:
            manifest = {"name": d.name}

        ws_yaml = d / "workspace.yaml"
        topo_count = 0
        if ws_yaml.exists():
            import yaml  # noqa: PLC0415

            yaml.safe_load(ws_yaml.read_text(encoding="utf-8"))
            topos_dir = d / "topologies"
            if topos_dir.exists():
                topo_count = len(list(topos_dir.glob("*.yaml")))

        installed.append(
            {
                "name": manifest.get("name", d.name),
                "path": str(d),
                "topologies": topo_count,
                "installed": manifest.get("installed_at", "unknown")[:19],
            }
        )

    if not installed:
        console.print(
            "[dim]No packages installed. Use 'swarmkit install <path>' to install one.[/dim]"
        )
        return

    table = Table(title="Installed packages")
    table.add_column("Package", style="bold")
    table.add_column("Topologies", justify="right")
    table.add_column("Installed", style="dim")
    table.add_column("Path", style="dim")

    for pkg in installed:
        table.add_row(
            pkg["name"],
            str(pkg["topologies"]),
            pkg["installed"],
            pkg["path"],
        )

    console.print(table)
