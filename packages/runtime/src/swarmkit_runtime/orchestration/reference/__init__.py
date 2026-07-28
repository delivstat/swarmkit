"""The bundled reference pipeline orchestrator — the saga *engine*
(design/details/bundled-pipeline-orchestrator.md).

The domain-neutral drive logic behind the `swarmkit orchestrator` command. This package (the
controller) is imported ONLY by that command — never by the runtime core or serve (import-linter
enforced). The saga *store* it drives is shared infra in ``swarmkit_runtime.orchestration``.
"""

from __future__ import annotations

from swarmkit_runtime.orchestration.reference._controller import ReferenceController

__all__ = ["ReferenceController"]
