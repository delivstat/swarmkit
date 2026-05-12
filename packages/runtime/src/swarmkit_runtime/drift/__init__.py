"""Intent drift detection — detect when agents wander from the original goal.

Optional per-agent monitoring that computes semantic similarity between
each agent step's output and the original intent. When drift exceeds
a threshold, the system can log, warn, or nudge the agent back.

See design/details/intent-drift-detection.md.
"""

from swarmkit_runtime.drift._config import IntentMonitoringConfig
from swarmkit_runtime.drift._observer import DriftResult, IntentObserver

__all__ = [
    "DriftResult",
    "IntentMonitoringConfig",
    "IntentObserver",
]
