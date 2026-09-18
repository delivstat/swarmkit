"""``swarmkit upgrade`` — detection, the breaking-change gate, and the install it drives.

No network, no real install: PyPI is stubbed via an httpx MockTransport, the install command is
captured by monkeypatching the runner, and detection is fed explicit inputs (upgrade-command.md).
"""

from __future__ import annotations

import httpx
import pytest
from swarmkit_runtime import _upgrade as up
from swarmkit_runtime._breaking_changes import BREAKING_CHANGES, changes_between
from swarmkit_runtime._versions import runtime_version
from typer.testing import CliRunner

from swarmkit_runtime.cli import app  # isort: skip


# ---- breaking-change data ---------------------------------------------------------------------


def test_breaking_changes_parse_and_are_not_ahead_of_this_runtime() -> None:
    current = runtime_version()
    for c in BREAKING_CHANGES:
        assert up.version_key(c.version)  # parses
        assert c.migration.startswith("http")
        if current:
            assert up.version_key(c.version) <= up.version_key(current), c.version


def test_changes_between_is_low_exclusive_high_inclusive() -> None:
    # 1.189.0 and 1.199.0 are the seeded breaking versions.
    assert [c.version for c in changes_between("1.180.0", "1.200.0")] == ["1.189.0", "1.199.0"]
    assert [c.version for c in changes_between("1.189.0", "1.199.0")] == [
        "1.199.0"
    ]  # low exclusive
    assert changes_between("1.199.0", "1.240.0") == []  # both already past
    assert changes_between("1.240.0", "1.240.0") == []  # not an upgrade


# ---- detection --------------------------------------------------------------------------------


def test_detect_method_from_the_interpreter_path() -> None:
    assert (
        up.detect_method("/home/u/.local/share/uv/tools/swarmkit-runtime/bin/python") == "uv-tool"
    )
    assert up.detect_method("/home/u/.local/pipx/venvs/swarmkit-runtime/bin/python") == "pipx"
    assert up.detect_method("/srv/app/.venv/bin/python", in_container=False) == "pip"
    assert up.detect_method("/usr/bin/python3", in_container=True) == "docker"


def test_detect_extras_reads_what_is_importable() -> None:
    present = {"swarmkit_webui", "psycopg", "anthropic"}
    extras = up.detect_extras(importable=lambda m: m in present)
    assert extras == ("ui", "postgres", "anthropic")
    assert up.detect_extras(importable=lambda _m: False) == ()


def test_the_install_command_carries_method_and_extras() -> None:
    assert up.upgrade_command("uv-tool", ("ui", "postgres")) == [
        "uv",
        "tool",
        "install",
        "--upgrade",
        "swarmkit-runtime[ui,postgres]",
    ]
    pipx = up.upgrade_command("pipx", ("ui",), "1.2.3")
    assert pipx is not None and pipx[:3] == ["pipx", "install", "--force"]
    assert pipx[-1] == "swarmkit-runtime[ui]==1.2.3"
    pip = up.upgrade_command("pip", ())
    assert pip is not None and pip[-2:] == ["-U", "swarmkit-runtime"]
    assert up.upgrade_command("docker", ("ui",)) is None
    assert up.upgrade_command("unknown", ()) is None


# ---- PyPI lookup ------------------------------------------------------------------------------


def _pypi(version: str, status: int = 200) -> httpx.MockTransport:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"info": {"version": version}})

    return httpx.MockTransport(handler)


def test_latest_version_reads_pypi() -> None:
    assert up.latest_version(transport=_pypi("1.242.0")) == "1.242.0"


def test_latest_version_offline_raises() -> None:
    def boom(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network")

    with pytest.raises(up.UpgradeError, match="--to"):
        up.latest_version(transport=httpx.MockTransport(boom))


# ---- the command ------------------------------------------------------------------------------


@pytest.fixture
def no_run(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture the install command instead of running it; fail loudly if run unexpectedly."""
    calls: list[list[str]] = []

    def _fake(cmd: list[str]) -> int:
        calls.append(cmd)
        return 0

    monkeypatch.setattr(up, "run_command", _fake)
    return calls


def _pin(
    monkeypatch: pytest.MonkeyPatch, *, installed: str, method: str, extras: tuple[str, ...]
) -> None:
    monkeypatch.setattr("swarmkit_runtime._versions.runtime_version", lambda: installed)
    monkeypatch.setattr(up, "detect_method", lambda *a, **k: method)
    monkeypatch.setattr(up, "detect_extras", lambda *a, **k: extras)


def test_check_exits_1_when_behind_and_never_installs(
    monkeypatch: pytest.MonkeyPatch, no_run: list[list[str]]
) -> None:
    _pin(monkeypatch, installed="1.240.0", method="uv-tool", extras=("ui",))
    out = CliRunner().invoke(app, ["upgrade", "--check", "--to", "1.245.0"])
    assert out.exit_code == 1, out.output
    assert "1.240.0 → 1.245.0" in out.output
    assert no_run == []  # --check never runs an installer


def test_check_exits_0_when_current(
    monkeypatch: pytest.MonkeyPatch, no_run: list[list[str]]
) -> None:
    _pin(monkeypatch, installed="1.245.0", method="uv-tool", extras=())
    out = CliRunner().invoke(app, ["upgrade", "--check", "--to", "1.245.0"])
    assert out.exit_code == 0 and "Nothing to do" in out.output
    assert no_run == []


def test_a_breaking_change_forces_the_prompt_and_no_aborts(
    monkeypatch: pytest.MonkeyPatch, no_run: list[list[str]]
) -> None:
    _pin(monkeypatch, installed="1.180.0", method="uv-tool", extras=("ui",))
    out = CliRunner().invoke(app, ["upgrade", "--to", "1.200.0"], input="n\n")
    assert out.exit_code == 0, out.output
    assert (
        "breaking change(s)" in out.output and "1.189.0" in out.output and "1.199.0" in out.output
    )
    assert "Nothing done." in out.output
    assert no_run == []  # declined → nothing installed


def test_yes_across_a_breaking_change_runs_the_detected_command(
    monkeypatch: pytest.MonkeyPatch, no_run: list[list[str]]
) -> None:
    _pin(monkeypatch, installed="1.180.0", method="uv-tool", extras=("ui", "postgres"))
    out = CliRunner().invoke(app, ["upgrade", "--to", "1.200.0", "--yes"])
    assert out.exit_code == 0, out.output
    assert no_run == [
        ["uv", "tool", "install", "--upgrade", "swarmkit-runtime[ui,postgres]==1.200.0"]
    ]


def test_docker_refuses_with_the_manual_command(
    monkeypatch: pytest.MonkeyPatch, no_run: list[list[str]]
) -> None:
    _pin(monkeypatch, installed="1.240.0", method="docker", extras=("ui",))
    out = CliRunner().invoke(app, ["upgrade", "--to", "1.245.0", "--yes"])
    assert out.exit_code != 0
    assert "container image" in out.output and "docker pull" in out.output
    assert no_run == []


def test_an_unknown_install_prints_the_pip_command(
    monkeypatch: pytest.MonkeyPatch, no_run: list[list[str]]
) -> None:
    _pin(monkeypatch, installed="1.240.0", method="unknown", extras=("ui",))
    out = CliRunner().invoke(app, ["upgrade", "--to", "1.245.0", "--yes"])
    assert out.exit_code != 0
    assert 'pip install -U "swarmkit-runtime[ui]==1.245.0"' in out.output
    assert no_run == []
