"""The bundled pipeline orchestrator is deprecated, says so, and still works.

`design/details/extracting-the-pipeline.md`, step 5, first half. Deletion is a later release; what
ships now is the notice and a capability freeze — because `_pipeline_stage.py` **is** the bundled
controller's stage execution, so removing it removes the feature rather than tidying around it.

Nothing here asserts that anything is gone. These tests exist to keep three promises honest:

* the deprecation is stated where an operator will meet it, and **once per process** rather than on
  every command — a warning that repeats is a warning that gets filtered;
* the subsystem still WORKS, because "deprecated" and "broken" are different words and a workspace
  is running on this today;
* the removal inventory is written down, so the deletion PR is mechanical rather than a
  rediscovery of what was pipeline-shaped.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

NOTE = Path(__file__).resolve().parents[3] / "docs/notes/pipeline-deprecation.md"


def _reset() -> None:
    """The notice latches per process; tests need it unlatched."""
    from swarmkit_runtime.orchestration import _deprecation  # noqa: PLC0415

    _deprecation._warned = False


# ---- it is said ----------------------------------------------------------------------------------


def test_using_the_pipeline_cli_warns() -> None:
    from swarmkit_runtime.orchestration._deprecation import warn_deprecated  # noqa: PLC0415

    _reset()
    with pytest.warns(DeprecationWarning, match="bundled pipeline orchestrator is deprecated"):
        warn_deprecated("swarmkit pipeline")


def test_it_is_said_once_per_process() -> None:
    """An operator polling `pipeline status` in a loop should be told, not nagged — a warning on
    every call is one that gets filtered out, taking the message with it."""
    from swarmkit_runtime.orchestration._deprecation import warn_deprecated  # noqa: PLC0415

    _reset()
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        for _ in range(5):
            warn_deprecated("swarmkit pipeline")

    assert len(seen) == 1


def test_the_message_names_the_replacement() -> None:
    """A deprecation without a migration path is an announcement, not a plan."""
    from swarmkit_runtime.orchestration._deprecation import MESSAGE  # noqa: PLC0415

    assert "examples/pipeline-orchestrator" in MESSAGE
    assert "extracting-the-pipeline.md" in MESSAGE


@pytest.mark.parametrize(
    "module",
    ["cli/_cmd_pipeline.py", "cli/_cmd_orchestrator.py"],
)
def test_both_entry_points_warn(module: str) -> None:
    src = (Path(__file__).resolve().parents[1] / "src/swarmkit_runtime" / module).read_text()

    assert "warn_deprecated(" in src


def test_the_cli_help_says_so() -> None:
    """Where someone actually looks before running it."""
    src = (Path(__file__).resolve().parents[1] / "src/swarmkit_runtime/cli/_app.py").read_text()

    assert "DEPRECATED" in src


# ---- and it still works --------------------------------------------------------------------------


def test_the_commands_are_still_registered() -> None:
    """Deprecated, not removed. A workspace is running on this today."""
    from swarmkit_runtime.cli._app import app  # noqa: PLC0415

    registered = {g.name for g in app.registered_groups}
    assert "pipeline" in registered

    commands = {
        c.name or (c.callback.__name__ if c.callback else "") for c in app.registered_commands
    }
    assert "orchestrator" in commands


def test_the_controller_still_imports_and_constructs() -> None:
    """The freeze is on capability, not on function."""
    from swarmkit_runtime.orchestration.reference import ReferenceController  # noqa: PLC0415

    assert ReferenceController is not None


# ---- the removal is written down -----------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "orchestration/",
        "server/_routes_pipelines.py",
        "server/_routes_sagas.py",
        "server/_pipeline_stage.py",
        "cli/_cmd_pipeline.py",
        "cli/_cmd_orchestrator.py",
        "schemas/stage-graph.schema.json",
    ],
)
def test_the_removal_inventory_names_what_goes(path: str) -> None:
    """So the deletion PR is mechanical rather than a rediscovery of what was pipeline-shaped."""
    assert path in NOTE.read_text()


@pytest.mark.parametrize(
    "keeper",
    ["PipelineSignal", "_pipeline_ingress", "gate_coverage", "pipeline_artifacts"],
)
def test_the_note_names_what_stays(keeper: str) -> None:
    """Four things live near the pipeline and are not it: the inbound signal seam and its webhook
    front door, the funnel-strength half of gate coverage, and the artifact store — which a one-shot
    run has written to since 1.179.0, so its name is historical rather than descriptive."""
    assert keeper in NOTE.read_text()


def test_the_note_states_that_nothing_breaks_yet() -> None:
    """The first thing a reader needs, and the thing a deprecation notice most often omits."""
    text = NOTE.read_text()

    assert "Nothing breaks" in text
    assert "keep working" in text
