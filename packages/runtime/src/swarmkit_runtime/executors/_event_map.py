"""The declarative event-map interpreter + launch-command substitutor (executor P3, PR2).

Pure, no IO — the heart of the declarative engine. Given a parsed JSON line from a harness's
``stdout`` and an :class:`~swarmkit_runtime.executors._adapter_spec.AdapterSpec`, produce normalized
:data:`ExecEvent`s; given the spec's launch template and a substitution context, produce the argv.

The DSL is intentionally minimal (RFC decision 1a): literal-equality matching, dotted-path (and
``for_each`` array) extraction, and one named-``map`` translation. No regex, conditionals, or
multi-line aggregation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from swarmkit_runtime.executors._adapter_spec import (
    AdapterSpec,
    EmitSpec,
    Rule,
    SubstitutionContext,
)
from swarmkit_runtime.executors._events import (
    ExecApprovalRequested,
    ExecArtifact,
    ExecEvent,
    ExecInputRequested,
    ExecMessage,
    ExecRaw,
    ExecResult,
    ExecStarted,
    ExecToolCall,
    ExecUsage,
)

# ---- extraction -------------------------------------------------------------------------------


def _get(obj: Any, dotted: str) -> Any:
    """Walk a dotted path over dict keys and list indices; ``None`` on any miss."""
    cur = obj
    for token in dotted.split("."):
        if token == "":
            continue
        if isinstance(cur, Mapping):
            cur = cur.get(token)
        elif isinstance(cur, (list, tuple)) and token.lstrip("-").isdigit():
            idx = int(token)
            cur = cur[idx] if -len(cur) <= idx < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


def _path(spec: str) -> str:
    """Normalize an extraction/match path: strip a leading ``$.`` (or ``$``)."""
    if spec.startswith("$."):
        return spec[2:]
    if spec.startswith("$"):
        return spec[1:]
    return spec


def _resolve(value: Any, obj: Any) -> Any:
    """A `with`/`set` value: ``$.``-string ⇒ extraction; other scalar ⇒ literal."""
    if isinstance(value, str) and value.startswith("$."):
        return _get(obj, _path(value))
    return value


def _matches(when: Mapping[str, Any], obj: Any) -> bool:
    """Literal-equality match: every dotted key resolves to its required literal."""
    return all(_get(obj, _path(key)) == expected for key, expected in when.items())


# ---- event construction -----------------------------------------------------------------------

_INT_FIELDS = frozenset({"input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens"})


def _coerce(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field in _INT_FIELDS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if field in ("cost_usd", "amount"):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return value


def _resolve_with(
    with_: Mapping[str, Any], obj: Any, status_map: Mapping[str, str]
) -> dict[str, Any]:
    """Resolve an emit's ``with`` block to concrete kwargs. A ``{from, map}`` value is extracted
    then translated through the named map (``_default`` covers the rest). ``None``s are dropped so
    the ExecEvent dataclass defaults apply."""
    out: dict[str, Any] = {}
    for field, spec in with_.items():
        if isinstance(spec, Mapping) and "from" in spec and "map" in spec:
            raw = _get(obj, _path(str(spec["from"])))
            table = status_map if spec["map"] == "status_map" else {}
            resolved = table.get(str(raw), table.get("_default"))
        else:
            resolved = _coerce(field, _resolve(spec, obj))
        if resolved is not None:
            out[field] = resolved
    return out


def _build_event(name: str, kwargs: Mapping[str, Any]) -> ExecEvent | None:  # noqa: PLR0911
    """Construct one ExecEvent from a resolved event name + kwargs (a dispatch table). Unknown/empty
    terminal shapes return ``None`` (skipped) so a partial line never crashes the run."""
    k = dict(kwargs)
    if name == "message":
        return ExecMessage(role=str(k.get("role", "assistant")), text=str(k.get("text", "")))
    if name == "tool_call":
        return ExecToolCall(
            tool=str(k.get("tool", "")),
            input_summary=str(k.get("input_summary", "")),
            status=str(k.get("status", "")),
        )
    if name == "usage":
        return ExecUsage(**{f: k[f] for f in k if f in _USAGE_FIELDS})
    if name == "artifact":
        return ExecArtifact(
            artifact_kind=str(k.get("artifact_kind", "structured")),  # type: ignore[arg-type]
            path=k.get("path"),
            ref=k.get("ref"),
            mime=k.get("mime"),
        )
    if name == "result":
        return ExecResult(
            status=str(k.get("status", "success")),  # type: ignore[arg-type]
            output=k.get("output"),
            exit_metadata=k.get("exit_metadata") or {},
        )
    if name == "approval_requested":
        return ExecApprovalRequested(
            run_id=str(k.get("run_id", "")),
            capability=str(k.get("capability", "")),
            rationale=k.get("rationale"),
        )
    if name == "input_requested":
        return ExecInputRequested(
            question=str(k.get("question", "")),
            options=tuple(k.get("options") or ()),
            question_class=k.get("question_class"),
        )
    if name == "started":
        return ExecStarted(run_id=str(k.get("run_id", "")), kind=str(k.get("kind", "")))
    if name == "raw":
        return ExecRaw(line=str(k.get("line", "")))
    return None


_USAGE_FIELDS = frozenset(
    {
        "unit",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "amount",
        "cost_usd",
    }
)


# ---- the interpreter --------------------------------------------------------------------------


def _collapse_results(events: list[ExecEvent]) -> list[ExecEvent]:
    """At most one terminal :class:`ExecResult` per output line — the LAST one declared wins.

    This is what lets a status be classified by *ordered literal-match rules* without conditionals:
    a base ``{type: result}`` rule sets ``success``, a later ``{type: result, is_error: true}`` rule
    overrides to ``failure``, and a still-later ``{subtype: error_max_turns}`` rule to
    ``budget_exceeded`` — most-specific-last wins (real harnesses signal errors across two fields,
    e.g. Claude Code's ``subtype: success`` + ``is_error: true``)."""
    results = [e for e in events if isinstance(e, ExecResult)]
    if len(results) <= 1:
        return events
    last = results[-1]
    return [e for e in events if not isinstance(e, ExecResult) or e is last]


class AdapterInterpreter:
    """Stateful translator: parsed JSON lines → ExecEvents, capturing ``session_id`` for resume."""

    def __init__(self, spec: AdapterSpec) -> None:
        self._spec = spec
        self.session_id: str | None = None

    def feed(self, obj: Mapping[str, Any]) -> list[ExecEvent]:
        events: list[ExecEvent] = []
        for rule in self._spec.event_map:
            if not _matches(rule.when, obj):
                continue
            self._apply_set(rule, obj)
            events.extend(self._apply_emit(rule, obj))
        return _collapse_results(events)

    def _apply_set(self, rule: Rule, obj: Mapping[str, Any]) -> None:
        for key, spec in rule.set.items():
            value = _resolve(spec, obj)
            if key == "session_id" and isinstance(value, str):
                self.session_id = value

    def _apply_emit(self, rule: Rule, obj: Mapping[str, Any]) -> list[ExecEvent]:
        if rule.for_each is not None:
            items = _get(obj, _path(rule.for_each))
            if not isinstance(items, (list, tuple)):
                return []
            out: list[ExecEvent] = []
            for item in items:
                out.extend(self._emit_all(rule.emit, item))
            return out
        return self._emit_all(rule.emit, obj)

    def _emit_all(self, emits: tuple[EmitSpec, ...], obj: Any) -> list[ExecEvent]:
        out: list[ExecEvent] = []
        for spec in emits:
            if spec.when and not _matches(spec.when, obj):
                continue
            kwargs = _resolve_with(spec.with_, obj, self._spec.status_map)
            event = _build_event(spec.event, kwargs)
            if event is not None:
                out.append(event)
        return out


# ---- launch-command substitution --------------------------------------------------------------


def _sub(template: str, ctx: SubstitutionContext) -> str:
    """Replace ``{var}`` occurrences with ``ctx[var]`` (empty when absent). Value-only — the result
    is a single argv element, never re-parsed by a shell."""
    out = template
    for var, value in ctx.items():
        out = out.replace("{" + var + "}", value)
    # Any unresolved vars (no value supplied) collapse to empty.
    while "{" in out and "}" in out:
        start = out.index("{")
        end = out.index("}", start)
        if end < start:
            break
        out = out[:start] + out[end + 1 :]
    return out


def build_command(
    spec: AdapterSpec, ctx: SubstitutionContext, *, resuming: bool = False
) -> list[str]:
    """Build the final argv from the launch template + a substitution context.

    ``optional_args`` groups are appended only when their ``when`` variable is set (non-empty) in
    ``ctx``; ``resume`` args are appended when ``resuming`` and a resume token is present. No shell.
    """
    argv = [_sub(part, ctx) for part in spec.launch.command]
    for group in spec.launch.optional_args:
        if ctx.get(group.when):
            argv.extend(_sub(a, ctx) for a in group.args)
    if resuming and ctx.get("resume.token") and spec.resume_arg:
        argv.extend(_sub(a, ctx) for a in spec.resume_arg)
    return argv
