"""There are no unimplemented subcommands left, and the facility that printed them still works.

See ``design/details/cli-unimplemented-stubs.md``. This file used to enumerate the commands that
were declared and did nothing, asserting each printed a clean message rather than a traceback. The
list is now empty: `swarmkit stop` was the last of them and landed in 1.193.0
(``design/details/stopping-a-run.md``).

Two things are worth keeping.

**The empty list is an assertion, not an absence.** A command that is registered, appears in
`--help`, and answers "not yet implemented" is the most polite version of the defect this repo keeps
finding — declared, discoverable, and connected to nothing. Asserting that none remain is how a new
one gets noticed at the moment it is added rather than by a user.

**The helper is still tested**, because the honest thing to do with a *future* stub is still to
print a clean message, and a facility nobody exercises rots.
"""

from __future__ import annotations

import pytest
from swarmkit_runtime.cli import app
from swarmkit_runtime.cli._common import _not_implemented
from typer.testing import CliRunner

runner = CliRunner()

#: Empty on purpose — see the module docstring. Add a case ONLY while a command is genuinely
#: stubbed, and delete it the moment the command lands.
STUBS: list[tuple[list[str], str]] = []


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


def test_no_command_is_a_stub_any_more() -> None:
    """The runtime source calls `_not_implemented` nowhere. `swarmkit stop` was the last one."""
    from pathlib import Path  # noqa: PLC0415

    src_root = Path(__file__).resolve().parents[1] / "src/swarmkit_runtime"
    callers = [
        path
        for path in src_root.rglob("*.py")
        if "_not_implemented(" in path.read_text() and path.name != "_common.py"
    ]

    assert callers == [], f"still stubbed: {[p.name for p in callers]}"


def test_the_clean_message_facility_still_works() -> None:
    """Kept because the right thing to do with a future stub is unchanged — and an untested
    facility is one that has quietly stopped working by the time it is needed."""
    import typer  # noqa: PLC0415

    with pytest.raises(typer.Exit) as exc:
        _not_implemented("example", milestone="M99")

    assert exc.value.exit_code == 2


def test_stop_is_a_real_command_now() -> None:
    """The one that graduated. Invoked against a directory with no workspace it fails on the store
    or the run id — either way it is doing work, not printing an apology."""
    result = runner.invoke(app, ["stop", "--help"])

    assert result.exit_code == 0
    combined = (result.stdout or "") + (result.stderr or "")
    assert "not yet implemented" not in combined
    assert "next agent boundary" in combined.replace("\n", " ").replace("  ", " ")
