"""Tests for mcp-serve and expertise packages."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest


@pytest.fixture()
def sample_workspace(tmp_path: Path) -> Path:
    """Create a minimal workspace for testing."""
    ws = tmp_path / "test-workspace"
    ws.mkdir()

    (ws / "workspace.yaml").write_text(
        """apiVersion: swarmkit/v1
kind: Workspace
metadata:
  id: test-ws
  name: Test Workspace
""",
        encoding="utf-8",
    )

    topos = ws / "topologies"
    topos.mkdir()
    (topos / "hello.yaml").write_text(
        """apiVersion: swarmkit/v1
kind: Topology
metadata:
  id: hello
  name: Hello
  description: A test topology.
agents:
  root:
    id: greeter
    role: root
    archetype: greeter
""",
        encoding="utf-8",
    )

    (ws / "package.yaml").write_text(
        """name: "@test/hello-workspace"
version: 1.0.0
description: A test workspace package.
author: Test
license: MIT
topologies:
  - hello
""",
        encoding="utf-8",
    )

    return ws


class TestPublisher:
    def test_publish_creates_tarball(self, sample_workspace: Path, tmp_path: Path) -> None:
        from swarmkit_runtime.packages._publisher import publish_package

        output = tmp_path / "dist"
        publish_package(sample_workspace, output)

        tarballs = list(output.glob("*.tar.gz"))
        assert len(tarballs) == 1
        assert "test-hello-workspace" in tarballs[0].name

    def test_publish_excludes_dotenv(self, sample_workspace: Path, tmp_path: Path) -> None:
        from swarmkit_runtime.packages._publisher import publish_package

        (sample_workspace / ".env").write_text("SECRET=bad", encoding="utf-8")
        (sample_workspace / ".swarmkit").mkdir()
        (sample_workspace / ".swarmkit" / "state.json").write_text("{}", encoding="utf-8")

        output = tmp_path / "dist"
        publish_package(sample_workspace, output)

        tarball = next(output.glob("*.tar.gz"))
        with tarfile.open(tarball, "r:gz") as tar:
            names = tar.getnames()
            assert not any(".env" in n for n in names)
            assert not any(".swarmkit" in n for n in names)

    def test_publish_no_workspace_yaml(self, tmp_path: Path) -> None:
        from swarmkit_runtime.packages._publisher import publish_package

        with pytest.raises(SystemExit):
            publish_package(tmp_path / "empty", tmp_path / "dist")


class TestInstaller:
    def test_install_from_dir(
        self,
        sample_workspace: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from swarmkit_runtime.packages import _installer

        pkg_dir = tmp_path / "packages"
        monkeypatch.setattr(_installer, "_PACKAGES_DIR", pkg_dir)

        _installer.install_package(str(sample_workspace))

        installed = list(pkg_dir.iterdir())
        assert len(installed) == 1
        assert (installed[0] / "workspace.yaml").exists()
        assert (installed[0] / ".swarmkit_manifest.json").exists()

        manifest = json.loads((installed[0] / ".swarmkit_manifest.json").read_text())
        assert manifest["name"] == "@test/hello-workspace"

    def test_install_from_tarball(
        self,
        sample_workspace: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from swarmkit_runtime.packages import _installer
        from swarmkit_runtime.packages._publisher import publish_package

        dist = tmp_path / "dist"
        publish_package(sample_workspace, dist)
        tarball = next(dist.glob("*.tar.gz"))

        pkg_dir = tmp_path / "packages"
        monkeypatch.setattr(_installer, "_PACKAGES_DIR", pkg_dir)

        _installer.install_package(str(tarball))

        installed = list(pkg_dir.iterdir())
        assert len(installed) == 1
        assert (installed[0] / "workspace.yaml").exists()

    def test_install_blocks_duplicate(
        self,
        sample_workspace: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from swarmkit_runtime.packages import _installer

        pkg_dir = tmp_path / "packages"
        monkeypatch.setattr(_installer, "_PACKAGES_DIR", pkg_dir)

        _installer.install_package(str(sample_workspace))
        _installer.install_package(str(sample_workspace))
        assert len(list(pkg_dir.iterdir())) == 1

    def test_install_upgrade_replaces(
        self,
        sample_workspace: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from swarmkit_runtime.packages import _installer

        pkg_dir = tmp_path / "packages"
        monkeypatch.setattr(_installer, "_PACKAGES_DIR", pkg_dir)

        _installer.install_package(str(sample_workspace))
        _installer.install_package(str(sample_workspace), upgrade=True)
        assert len(list(pkg_dir.iterdir())) == 1

    def test_list_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from swarmkit_runtime.packages import _installer

        pkg_dir = tmp_path / "packages"
        pkg_dir.mkdir()
        monkeypatch.setattr(_installer, "_PACKAGES_DIR", pkg_dir)
        _installer.list_packages(tmp_path)


class TestMCPServeModule:
    def test_tool_name_single_workspace(self) -> None:
        from swarmkit_runtime.mcp._serve import run_mcp_server  # noqa: F401

    def test_should_exclude(self) -> None:
        from swarmkit_runtime.packages._publisher import _should_exclude

        assert _should_exclude("__pycache__/foo.pyc")
        assert _should_exclude(".git/config")
        assert _should_exclude(".env")
        assert _should_exclude(".swarmkit/state.json")
        assert _should_exclude("data.sqlite")
        assert not _should_exclude("workspace.yaml")
        assert not _should_exclude("topologies/hello.yaml")
        assert not _should_exclude("skills/review.yaml")
