"""Which run the current task belongs to.

A `ContextVar`, not a module global, for the same reason the active trace is one: several jobs run
concurrently in a single ``swarmkit serve`` process, and asyncio copies the context when a task is
created — so each run sees its own value and concurrent runs cannot clobber each other.

This exists because audit events had no way to say which run they came from. `_extract_events`
drained the governance provider's ENTIRE accumulated log on every run and
`_persist_events_to_audit` stamped all of it with the current run's id, so a second run in the same
process re-persisted the first run's events under its own `run_id` and `topology_id`::

    run 1 (triage) drains: [('skill.executed', 'triage')]
    run 2 (design) drains: [('skill.executed', 'triage'), ('skill.executed', 'designer')]

Every event was rewritten once per subsequent run, growing quadratically, attributed to whichever
run happened to be last. Reading `audit_events` by `run_id` — the obvious way to answer "what did
this run cost" — returned a confident, wrong answer, and so did joining on `topology_id`.

Slicing the provider's list by length instead would have been wrong under exactly the case that
matters: two concurrent jobs interleave their appends, so a tail slice mixes them. Attribution has
to be recorded when the event is emitted, by the task that emits it. That is what this is.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_current_run_id: ContextVar[str | None] = ContextVar("swarmkit_current_run_id", default=None)
#: Caller-supplied {key: value} for the current run. Opaque to the runtime by design: a caller
#: groups runs by whatever it is modelling — a Wayfinder map, a requirement, a tenant — and
#: SwarmKit never has to learn what any of those are. Same per-task scoping as the run id.
#: `None`, not `{}` — a mutable ContextVar default is shared by every task that never sets one.
_current_labels: ContextVar[dict[str, str] | None] = ContextVar(
    "swarmkit_current_labels", default=None
)


def current_run_id() -> str | None:
    """The run the calling task belongs to, or None outside a run.

    Used as the ``default_factory`` for :attr:`AuditEvent.run_id`, so every event is stamped at
    construction without each emitter having to know or pass it.
    """
    return _current_run_id.get()


def current_labels() -> dict[str, str]:
    """The calling task's run labels, or an empty dict outside a run.

    Used as the ``default_factory`` for :attr:`AuditEvent.labels`, so a caller's grouping reaches
    the audit log without any emitter knowing what the labels mean.
    """
    return dict(_current_labels.get() or {})


def set_current_labels(labels: dict[str, str] | None) -> Token[dict[str, str] | None]:
    """Enter a label scope. Pass the returned token to :func:`reset_current_labels`."""
    return _current_labels.set(dict(labels or {}))


def reset_current_labels(token: Token[dict[str, str] | None]) -> None:
    """Leave a label scope."""
    _current_labels.reset(token)


def set_current_run_id(run_id: str | None) -> Token[str | None]:
    """Enter a run scope. Pass the returned token to :func:`reset_current_run_id`."""
    return _current_run_id.set(run_id)


def reset_current_run_id(token: Token[str | None]) -> None:
    """Leave a run scope, restoring whatever was in effect before."""
    _current_run_id.reset(token)
