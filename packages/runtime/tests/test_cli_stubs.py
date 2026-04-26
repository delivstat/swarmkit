"""Unimplemented subcommands print a clean message instead of a traceback.

See ``design/details/cli-unimplemented-stubs.md``. When a subcommand lands
for real, drop its case from STUBS and add an integration test for the
implementation — this file should only assert on commands that are
genuinely stubbed right now.
"""

from __future__ import annotations

import pytest
from swarmkit_runtime.cli import app
from typer.testing import CliRunner

runner = CliRunner()

STUBS: list[tuple[list[str], str]] = [
    (["eject", "some-topology.yaml"], "eject"),
]


@pytest.mark.parametrize(("argv", "command_label"), STUBS)
def test_stub_subcommand_prints_clean_message(argv: list[str], command_label: str) -> None:
    result = runner.invoke(app, argv)

    assert result.exit_code == 2, result.output
    combined = (result.stdout or "") + (result.stderr or "")
    assert "not yet implemented" in combined
    assert f"swarmkit {command_label}" in combined
    # No Python traceback should leak through — that's the whole point.
    assert "Traceback" not in combined
    assert "NotImplementedError" not in combined
