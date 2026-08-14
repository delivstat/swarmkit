"""The bundled pipeline orchestrator is deprecated.

``design/details/extracting-the-pipeline.md``. SwarmKit runs a swarm over an input and returns a
governed, approved artifact; deciding what runs next belongs to the application. The bundled
sequencer was a zero-config convenience and it grew into a saga engine — durable state, event
dedup, lease reclaim, crash reconciliation — which is a distributed-systems problem mature engines
already solve and not SwarmKit's differentiator.

**Nothing breaks today.** This subsystem keeps working and keeps getting bug fixes for at least one
release. What stops is growth: no event routing, no fan-out, no cycles. A workspace that needs those
is one that should own its sequencing, and that signal is more useful than the feature.

The replacement is ``examples/pipeline-orchestrator`` — a sequencer over the public HTTP API with no
runtime import, in about 180 lines. Everything it needs now exists: correlated runs (1.176.0,
1.187.0 over HTTP), retrievable artifacts and diffs (1.179.0, 1.183.0-1.185.0), gates that park
per-run and resume (1.181.0-1.182.0, 1.186.0).

The warning is emitted once per process, not per call: an operator running
`swarmkit pipeline status` in a loop should be told, not nagged.
"""

from __future__ import annotations

import logging
import warnings

_logger = logging.getLogger("swarmkit.orchestration")

MESSAGE = (
    "The bundled pipeline orchestrator is deprecated and will be removed. Sequencing belongs to "
    "the application — see examples/pipeline-orchestrator for a ~180-line replacement over the "
    "HTTP API, and design/details/extracting-the-pipeline.md for why. Existing pipelines keep "
    "working; no new capability will be added."
)

_warned = False


def warn_deprecated(surface: str) -> None:
    """Say it once per process, naming which surface was used."""
    global _warned  # noqa: PLW0603
    if _warned:
        return
    _warned = True
    _logger.warning("%s (used: %s)", MESSAGE, surface)
    warnings.warn(MESSAGE, DeprecationWarning, stacklevel=2)
