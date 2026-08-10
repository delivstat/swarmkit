"""A correlated one-shot run can put its output somewhere durable, and read it back.

`pipeline_artifacts` is keyed `<correlation>/<stage>/<name>` and only pipeline stage code ever wrote
to it, so a `swarmkit run` resolving a ticket had nowhere for its output but a shell redirect.

The store itself was never pipeline-only — `ArtifactStore` is a Protocol with three backends
(database / filesystem / s3) and the storage service already resolves it. What was missing was a
caller.

**The ref shape.** A one-shot run has a correlation id but no stage, so the RUN id goes in the
middle segment: `<correlation>/<run-id>/output`. Three parts, unchanged format, and the run id is
what keeps retries and re-runs under one correlation from overwriting each other. It is also
`jobs.id` and
`audit_events.run_id`, so a reader holding one holds the others — the whole point of the chain this
batch has been repairing.

**Reading is part of the feature.** Shipping a write path with no way to fetch it back would be a
write-only store: configuration that exists, is accepted, and reaches nobody — the exact defect this
codebase has spent a week fixing. `swarmkit artifacts list|get` ships with it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from swarmkit_runtime.artifacts import artifact_ref, build_artifact_store
from swarmkit_runtime.cli import _cmd_run


def _store(tmp_path: Path) -> Any:
    return build_artifact_store(
        {"backend": "filesystem"},
        workspace_root=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'a.sqlite'}",
    )


# ---- the ref shape -----------------------------------------------------------------------------


def test_a_run_artifact_is_addressed_by_correlation_and_run() -> None:
    """Three parts, unchanged format: the run takes the slot a stage would."""
    assert artifact_ref("WMS-30", "run-abc") == "WMS-30/run-abc/output"


def test_two_runs_under_one_correlation_do_not_collide(tmp_path: Path) -> None:
    """The reason the run id is the middle segment rather than a literal like "run".

    A Wayfinder map has many resolution runs under one correlation, and a retry produces a second
    run for the same ticket. A fixed middle segment would silently overwrite the first.
    """
    store = _store(tmp_path)
    store.put("WMS-30", "run-1", "first")
    store.put("WMS-30", "run-2", "second")

    assert store.get("WMS-30/run-1/output") == "first"
    assert store.get("WMS-30/run-2/output") == "second"


def test_artifacts_are_listable_by_correlation(tmp_path: Path) -> None:
    """What the correlation id is for: everything one ticket produced, in one call."""
    store = _store(tmp_path)
    store.put("WMS-30", "run-1", "a")
    store.put("WMS-30", "run-2", "b")
    store.put("WMS-31", "run-3", "c")

    assert sorted(store.list("WMS-30")) == ["WMS-30/run-1/output", "WMS-30/run-2/output"]


# ---- the write path ----------------------------------------------------------------------------


def test_a_run_persists_its_output(tmp_path: Path) -> None:
    _cmd_run._persist_artifact(tmp_path, "WMS-30", "run-1", "the resolution")

    from swarmkit_runtime.persistence import storage_for_workspace  # noqa: PLC0415

    store = storage_for_workspace(tmp_path).artifact_store()
    assert store.get("WMS-30/run-1/output") == "the resolution"


def test_the_ref_is_printed_so_a_caller_can_use_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A durable output nobody is told the address of is not durable in any useful sense."""
    _cmd_run._persist_artifact(tmp_path, "WMS-30", "run-1", "x")

    assert "WMS-30/run-1/output" in capsys.readouterr().out


def test_an_empty_output_is_still_written(tmp_path: Path) -> None:
    """ "The run produced nothing" is a result, and a missing artifact cannot express it."""
    _cmd_run._persist_artifact(tmp_path, "WMS-30", "run-1", "")

    from swarmkit_runtime.persistence import storage_for_workspace  # noqa: PLC0415

    assert storage_for_workspace(tmp_path).artifact_store().get("WMS-30/run-1/output") == ""


def test_a_storage_failure_does_not_lose_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run already succeeded, printed its output and closed its job row. Raising here would
    throw that away over a storage problem."""

    def _boom(_root: Path) -> Any:
        raise RuntimeError("artifact backend down")

    monkeypatch.setattr("swarmkit_runtime.persistence.storage_for_workspace", _boom)

    _cmd_run._persist_artifact(tmp_path, "WMS-30", "run-1", "x")

    assert "could not save the artifact" in capsys.readouterr().err


def test_no_correlation_writes_nothing(tmp_path: Path) -> None:
    """Addressed by correlation, so one written under an invented id could not be listed."""
    _cmd_run._persist_artifact(tmp_path, None, "run-1", "x")

    from swarmkit_runtime.persistence import storage_for_workspace  # noqa: PLC0415

    assert storage_for_workspace(tmp_path).artifact_store().list("run-1") == []


def test_the_cli_refuses_before_the_run_costs_anything() -> None:
    """`--save-artifact` without `--correlation-id` used to run the topology first and fail after.

    A usage error that arrives after a paid run is a usage error that charged for itself.
    """
    from swarmkit_runtime.cli._app import app  # noqa: PLC0415
    from typer.testing import CliRunner  # noqa: PLC0415

    result = CliRunner().invoke(app, ["run", ".", "hello", "-i", "x", "--save-artifact"])

    assert result.exit_code == 2
    assert "needs --correlation-id" in result.output


# ---- the read path -----------------------------------------------------------------------------


def test_list_prints_each_ref(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from swarmkit_runtime.cli._cmd_artifacts import list_artifacts  # noqa: PLC0415

    _cmd_run._persist_artifact(tmp_path, "WMS-30", "run-1", "a")
    capsys.readouterr()

    list_artifacts("WMS-30", tmp_path)

    assert "WMS-30/run-1/output" in capsys.readouterr().out


def test_list_says_so_when_there_is_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rather than printing nothing, which reads like a broken command."""
    from swarmkit_runtime.cli._cmd_artifacts import list_artifacts  # noqa: PLC0415

    list_artifacts("WMS-99", tmp_path)

    assert "no artifacts" in capsys.readouterr().out


def test_get_prints_the_content(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from swarmkit_runtime.cli._cmd_artifacts import get_artifact  # noqa: PLC0415

    _cmd_run._persist_artifact(tmp_path, "WMS-30", "run-1", "the resolution")
    capsys.readouterr()

    get_artifact("WMS-30/run-1/output", tmp_path)

    assert capsys.readouterr().out == "the resolution"


def test_get_exits_non_zero_for_a_missing_ref(tmp_path: Path) -> None:
    """So a shell pipeline does not carry an empty string forward as though it were the output."""
    import typer  # noqa: PLC0415
    from swarmkit_runtime.cli._cmd_artifacts import get_artifact  # noqa: PLC0415

    with pytest.raises(typer.Exit) as exc:
        get_artifact("WMS-30/nope/output", tmp_path)

    assert exc.value.exit_code == 1


def test_the_configured_backend_is_honoured(tmp_path: Path) -> None:
    """The one resolver, not a second store: a workspace choosing `filesystem` must not have its
    run artifacts quietly written to the database instead."""
    import yaml  # noqa: PLC0415

    (tmp_path / "workspace.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Workspace",
                "metadata": {"id": "w", "name": "w"},
                "storage": {"artifacts": {"backend": "filesystem"}},
            }
        )
    )

    _cmd_run._persist_artifact(tmp_path, "WMS-30", "run-1", "on disk")

    written = list((tmp_path / ".swarmkit" / "artifacts").rglob("*"))
    assert any(p.is_file() for p in written), "the filesystem backend must have been used"
