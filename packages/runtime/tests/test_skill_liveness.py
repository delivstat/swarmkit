"""The liveness check: does each MCP-backed skill's tool still exist?

A curated skill list nobody re-checks becomes an awesome-list, and those rot in months — a server
renames a tool, changes an argument, or disappears, and the entry keeps claiming it works. This is
what makes `design/details/skill-catalogue.md` worth building at all: SwarmKit can start the server
and ask, so "pre-validated" becomes "verified on a date" rather than a promise.

The unit tests here pin the classification, which is where the judgement lives. The end-to-end
check against the real first-party servers runs nightly, not on every PR — a third-party outage
must never block a merge.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).parents[3]
_spec = importlib.util.spec_from_file_location(
    "check_skill_liveness", REPO / "scripts" / "check_skill_liveness.py"
)
assert _spec and _spec.loader
_m = importlib.util.module_from_spec(_spec)
sys.modules["check_skill_liveness"] = _m
_spec.loader.exec_module(_m)


class _Cfg:
    """The fields `_needs_credentials` reads from an MCPServerConfig."""

    def __init__(self, credentials_ref: str = "", env: dict[str, str] | None = None) -> None:
        self.credentials_ref = credentials_ref
        self.env = env or {}


class TestUnverifiableIsDetectedNotGuessed:
    """The most-wanted entries — GitHub, Slack — are exactly the ones public CI cannot check. A
    green badge meaning "we did not look" is worse than no badge."""

    def test_a_declared_credential_makes_it_unverifiable(self) -> None:
        assert "github-pat" in _m._needs_credentials(_Cfg(credentials_ref="github-pat"))

    def test_an_unset_env_interpolation_makes_it_unverifiable(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        why = _m._needs_credentials(_Cfg(env={"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"}))
        assert "GITHUB_TOKEN" in why

    def test_a_set_env_interpolation_is_checkable(self, monkeypatch: Any) -> None:
        """With the credential present the entry becomes checkable — the state is about this
        environment, not about the entry forever."""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
        assert _m._needs_credentials(_Cfg(env={"X": "${GITHUB_TOKEN}"})) == ""

    def test_a_server_needing_nothing_is_checkable(self) -> None:
        assert _m._needs_credentials(_Cfg()) == ""

    def test_a_literal_env_value_is_not_a_credential(self, monkeypatch: Any) -> None:
        """`env: { LOG_LEVEL: debug }` must not make an entry unverifiable."""
        assert _m._needs_credentials(_Cfg(env={"LOG_LEVEL": "debug"})) == ""


class TestOnlyBrokenFails:
    """`unverifiable` failing the build would make every credentialed entry permanently red, and
    teach everyone to ignore the check — the exact fate this design is trying to avoid."""

    @pytest.mark.parametrize(
        ("states", "expected"),
        [
            (["verified", "verified"], 0),
            (["verified", "unverifiable"], 0),
            (["unverifiable"], 0),
            (["verified", "broken"], 1),
            (["broken"], 1),
        ],
    )
    def test_exit_code(self, states: list[str], expected: int, monkeypatch: Any) -> None:
        results = [_m.Result(f"s{i}", "srv", "tool", st) for i, st in enumerate(states)]
        monkeypatch.setattr(_m.asyncio, "run", lambda _coro: results)
        monkeypatch.setattr(sys, "argv", ["check_skill_liveness.py"])
        assert _m.main() == expected


class TestTheReportSaysWhatToDo:
    def test_a_broken_result_carries_its_reason(self) -> None:
        """A fixer — a person or a swarm picking up the filed issue — needs the reason, not just
        the fact. "tool not listed; server offers: …" names the likely rename."""
        r = _m.Result(
            "get-schema",
            "k",
            "get_schema_v2",
            "broken",
            "tool not listed; server offers: get_schema…",
        )
        assert r.mark == "✗"
        assert "get_schema" in r.detail

    def test_each_state_has_a_distinct_mark(self) -> None:
        marks = {_m.Result("s", "v", "t", st).mark for st in ("verified", "broken", "unverifiable")}
        assert len(marks) == 3
