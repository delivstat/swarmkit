"""Eval harness error taxonomy."""

from __future__ import annotations


class EvalError(Exception):
    """Base class for eval-harness errors."""


class EvalSetNotFoundError(EvalError):
    """No eval-set matched the given id/path under the workspace."""


class EvalSetInvalidError(EvalError):
    """An eval-set file failed to parse/validate."""
