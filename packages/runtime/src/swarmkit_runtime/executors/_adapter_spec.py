"""Typed, resolved form of a declarative executor-adapter (executor-declarative-adapters-plan.md).

Parsed from an already schema-validated ``ExecutorAdapter`` artifact (PR1) into frozen dataclasses
the interpreter (`_event_map.py`) and the `DeclarativeExecutor` (PR3) consume. Parsing is total —
the schema guarantees shape, so this never validates, only shapes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OptionalArgs:
    """A launch arg group appended only when ``when`` (a substitution var name) is set."""

    when: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class AuthMode:
    """What one auth mode contributes to the launch (env / args / provisioned credential paths)."""

    env: Mapping[str, str] = field(default_factory=dict)
    args: tuple[str, ...] = ()
    credential_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class AuthSpec:
    default: str | None = None
    modes: Mapping[str, AuthMode] = field(default_factory=dict)


@dataclass(frozen=True)
class LaunchSpec:
    command: tuple[str, ...]
    optional_args: tuple[OptionalArgs, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EmitSpec:
    """Emit one ExecEvent. ``when`` is an optional per-item match (inside ``for_each``); ``with_``
    maps event fields to extraction paths / literals / a ``{from, map}`` translation."""

    event: str
    when: Mapping[str, Any] = field(default_factory=dict)
    with_: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    """One event-map rule: match a line, optionally iterate an array, capture state, emit events."""

    when: Mapping[str, Any] = field(default_factory=dict)
    for_each: str | None = None
    set: Mapping[str, Any] = field(default_factory=dict)
    emit: tuple[EmitSpec, ...] = ()


@dataclass(frozen=True)
class AdapterSpec:
    """The runnable shape of a declarative adapter."""

    kind: str
    launch: LaunchSpec
    event_map: tuple[Rule, ...]
    status_map: Mapping[str, str] = field(default_factory=dict)
    resume_arg: tuple[str, ...] = ()
    auth: AuthSpec = field(default_factory=AuthSpec)
    on_unanswerable: str = "abort"
    # Interaction (RFC §6.2), present only when on_unanswerable == "relay": the bidirectional driver
    # that feeds an approval decision back, and the bounded wait before degrading to abort.
    interaction_driver: str | None = None
    max_approval_wait_seconds: float | None = None
    telemetry_grade: str = "normalized"
    artifacts_profile: str = "files"
    retain_raw: bool = False
    success_exit_code: int | None = None
    requires_code: bool = False


def _emit(raw: Mapping[str, Any]) -> EmitSpec:
    return EmitSpec(
        event=str(raw["event"]),
        when=dict(raw.get("when") or {}),
        with_=dict(raw.get("with") or {}),
    )


def _rule(raw: Mapping[str, Any]) -> Rule:
    return Rule(
        when=dict(raw.get("when") or {}),
        for_each=raw.get("for_each"),
        set=dict(raw.get("set") or {}),
        emit=tuple(_emit(e) for e in raw.get("emit") or ()),
    )


def _auth(raw: Mapping[str, Any] | None) -> AuthSpec:
    if not raw:
        return AuthSpec()
    modes = {
        name: AuthMode(
            env=dict(m.get("env") or {}),
            args=tuple(m.get("args") or ()),
            credential_paths=tuple(m.get("credential_paths") or ()),
        )
        for name, m in (raw.get("modes") or {}).items()
    }
    return AuthSpec(default=raw.get("default"), modes=modes)


def parse_adapter_spec(raw: Mapping[str, Any]) -> AdapterSpec:
    """Shape a schema-validated ``ExecutorAdapter`` artifact into an :class:`AdapterSpec`."""
    spec = raw["spec"]
    launch_raw = spec["launch"]
    launch = LaunchSpec(
        command=tuple(launch_raw["command"]),
        optional_args=tuple(
            OptionalArgs(when=o["when"], args=tuple(o["args"]))
            for o in launch_raw.get("optional_args") or ()
        ),
        env=dict(launch_raw.get("env") or {}),
    )
    resume = spec.get("resume") or {}
    success_when: Mapping[str, Any] = spec.get("success_when") or {}
    interaction: Mapping[str, Any] = spec.get("interaction") or {}
    return AdapterSpec(
        kind=str(raw["metadata"]["id"]),
        launch=launch,
        event_map=tuple(_rule(r) for r in spec["event_map"]),
        status_map=dict(spec.get("status_map") or {}),
        resume_arg=tuple(resume.get("arg") or ()),
        auth=_auth(spec.get("auth")),
        on_unanswerable=str(spec.get("on_unanswerable", "abort")),
        interaction_driver=interaction.get("driver"),
        max_approval_wait_seconds=interaction.get("max_approval_wait_seconds"),
        telemetry_grade=str(spec.get("telemetry_grade", "normalized")),
        artifacts_profile=str((spec.get("artifacts") or {}).get("profile", "files")),
        retain_raw=bool((spec.get("stream") or {}).get("retain_raw", False)),
        success_exit_code=success_when.get("exit_code"),
        requires_code=spec.get("requires") == "code",
    )


# The substitution-variable context the launch template consumes. Keys are the dotted var names
# used in braces, e.g. "task.statement", "budget.max_turns", "resume.token".
SubstitutionContext = Mapping[str, str]
