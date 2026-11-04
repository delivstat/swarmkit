"""Eval harness error taxonomy."""

from __future__ import annotations


class EvalError(Exception):
    """Base class for eval-harness errors."""


class EvalSetNotFoundError(EvalError):
    """No eval-set matched the given id/path under the workspace."""


class EvalSetInvalidError(EvalError):
    """An eval-set file failed to parse/validate."""


class EvalTargetNotFoundError(EvalError):
    """The eval-set's target names no topology in the workspace."""


class EvalRunNotFoundError(EvalError):
    """No eval run has the given id."""


class EvalRunConflictError(EvalError):
    """The eval run is in a state the request can't act on — e.g. resuming a finished run."""
