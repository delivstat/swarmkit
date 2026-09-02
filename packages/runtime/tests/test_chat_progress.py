"""A harness must be visible while it runs, on every surface a person watches.

`harness-progress-stream.md` exists because a multi-minute claude-code run emitted nothing until it
finished, and silence is indistinguishable from a hang. It fixed `swarmkit run --verbose` and
serve's job records — and left two surfaces dark, both of them chat:

* **`swarmkit chat`** never subscribed to the progress sink at all.
* **The portal's chat** subscribed to a *different bus*. A model agent publishes through
  `_helpers._progress`; a harness publishes through `progress.emit_progress`. Nothing joined them,
  so the portal showed live steps for a model node and nothing whatsoever for a harness.

The second is the one that would survive a casual fix, because the portal *looked* wired: it had a
listener, a thinking indicator and an expandable step log, and every one of them worked — for the
node type that was never the problem.
"""

from __future__ import annotations

import pytest
from swarmkit_runtime.cli._cmd_chat import _turn_progress
from swarmkit_runtime.langgraph_compiler._helpers import _progress, progress_listener
from swarmkit_runtime.progress import ProgressEvent, emit_progress


def _harness_event(**kw: object) -> ProgressEvent:
    base: dict[str, object] = {
        "agent_id": "builder",
        "kind": "message",
        "summary": "Reading orders.py",
        "detail": "Reading orders.py to find the retry path.",
    }
    base.update(kw)
    return ProgressEvent(**base)  # type: ignore[arg-type]


class TestTheTwoBusesAreJoined:
    def test_harness_progress_reaches_a_portal_listener(self) -> None:
        """The bug: the portal's SSE stream reads `_helpers`, the harness writes `progress`."""
        seen: list[str] = []
        with progress_listener(seen.append):
            emit_progress(_harness_event())
        assert any("Reading orders.py" in line for line in seen)

    def test_model_progress_still_reaches_it(self) -> None:
        """The path that already worked must keep working."""
        seen: list[str] = []
        with progress_listener(seen.append):
            _progress("  [analyst] calling read_file")
        assert any("read_file" in line for line in seen)

    def test_the_shared_bus_never_carries_detail(self) -> None:
        """A harness message can quote a file, and a file can quote a credential. serve publishes
        this bus to anyone with `serve:read`, so it gets the bounded summary and nothing else."""
        seen: list[str] = []
        with progress_listener(seen.append):
            emit_progress(_harness_event(summary="Reading config", detail="API_KEY=sk-live-secret"))
        assert seen, "the event must arrive"
        assert not any("sk-live-secret" in line for line in seen)

    def test_a_listener_that_raises_does_not_fail_the_run(self) -> None:
        """Bad observability is not worth losing work over — the rule the module opens with."""

        def _explode(_: str) -> None:
            raise RuntimeError("subscriber bug")

        with progress_listener(_explode):
            emit_progress(_harness_event())  # must not raise

    def test_no_listener_is_not_an_error(self) -> None:
        emit_progress(_harness_event())  # outside any listener context


class TestChatSubscribes:
    def test_a_turn_prints_harness_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """`swarmkit run` gates this behind --verbose to keep stdout parseable for scripts. Chat
        has no such contract: it is interactive, and a blank prompt through a four-minute harness
        run is the failure the design note exists to remove."""
        with _turn_progress():
            emit_progress(_harness_event())
        assert "Reading orders.py" in capsys.readouterr().err

    def test_it_prints_detail_not_only_the_summary(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A local terminal already holds the workspace and its credentials — a different blast
        radius from a shared job record. Same reasoning `run --verbose` uses."""
        with _turn_progress():
            emit_progress(_harness_event(summary="Reading", detail="the full sentence it wrote"))
        assert "the full sentence it wrote" in capsys.readouterr().err

    def test_progress_goes_to_stderr_so_the_answer_stays_on_stdout(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with _turn_progress():
            emit_progress(_harness_event())
        captured = capsys.readouterr()
        assert "Reading orders.py" in captured.err
        assert "Reading orders.py" not in captured.out

    def test_the_sink_does_not_outlive_the_turn(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Scoped to the turn, so a sink can never be left attached to a finished run."""
        with _turn_progress():
            emit_progress(_harness_event())
        capsys.readouterr()
        emit_progress(_harness_event(summary="after the turn", detail="after the turn"))
        assert "after the turn" not in capsys.readouterr().err
