"""Workspace resolver - orchestrates phases 1-4 of the M1 pipeline.

Phase 1: discovery  (:mod:`swael_runtime.workspace`)
Phase 2: schema validation  (this module, :func:`validate_discovered`)
Phase 3: resolution
  - 3a skill registry        (:mod:`swael_runtime.skills`)
  - 3b archetype registry    (:mod:`swael_runtime.archetypes`)
  - 3c topology resolution   (``_topology.build_topology_registry``)
  - 3d trigger resolution    (``_triggers.build_trigger_registry``)
Phase 4: :class:`ResolvedWorkspace` construction (:func:`resolve_workspace`)

:func:`resolve_workspace` is the single public entry point. Both the
``swarmkit validate`` CLI (M1.6) and the runtime go through it.

Design reference: ``design/details/topology-loader.md``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from jsonschema import ValidationError
from pydantic import ValidationError as PydanticValidationError
from swael_schema import SchemaName, validate
from swael_schema.models import SwarmKitWorkspace

from swael_runtime.archetypes import build_archetype_registry
from swael_runtime.errors import ResolutionError, ResolutionErrors, yaml_pointer
from swael_runtime.skills import build_skill_registry
from swael_runtime.workspace import (
    ArtifactKind,
    ArtifactKindMismatchError,
    DeepNestingError,
    DiscoveredArtifact,
    DiscoveryError,
    MalformedArtifactError,
    MissingWorkspaceFileError,
    WorkspaceNotFoundError,
    YAMLParseError,
    discover,
)

from ._resolved import (
    AgentRole,
    ResolvedAgent,
    ResolvedTopology,
    ResolvedTrigger,
    ResolvedWorkspace,
)
from ._topology import build_topology_registry
from ._triggers import build_trigger_registry

# jsonschema validator keyword → SwarmKit error code.
# The keyword is which JSON-Schema rule the artifact violated. Unmapped
# keywords fall through to ``schema.other``.
_VALIDATOR_TO_CODE: dict[str, str] = {
    "required": "schema.required-field",
    "type": "schema.type-mismatch",
    "pattern": "schema.pattern-mismatch",
    "enum": "schema.enum-mismatch",
    "const": "schema.const-mismatch",
    "minLength": "schema.length",
    "maxLength": "schema.length",
    "minimum": "schema.range",
    "maximum": "schema.range",
    "minItems": "schema.array-size",
    "maxItems": "schema.array-size",
    "minProperties": "schema.object-size",
    "maxProperties": "schema.object-size",
    "additionalProperties": "schema.unknown-field",
    "oneOf": "schema.variant-mismatch",
    "anyOf": "schema.variant-mismatch",
    "allOf": "schema.conditional",
    "if": "schema.conditional",
    "then": "schema.conditional",
    "else": "schema.conditional",
    "format": "schema.format",
}


def validate_discovered(
    artifacts: Iterable[DiscoveredArtifact],
) -> list[ResolutionError]:
    """Run JSON-Schema validation across every discovered artifact.

    Returns a list of :class:`ResolutionError`. Empty list means every
    artifact validates. The caller (``resolve_workspace`` in M1.5) wraps
    the list in :class:`ResolutionErrors` if non-empty.
    """
    errors: list[ResolutionError] = []
    for artifact in artifacts:
        try:
            validate(_to_schema_name(artifact.kind), dict(artifact.raw))
        except ValidationError as exc:
            errors.append(_from_jsonschema(artifact, exc))
    return errors


def _bridge_workspace_not_found(exc: WorkspaceNotFoundError) -> ResolutionError:
    return ResolutionError(
        code="workspace.not-found",
        message=str(exc),
        artifact_path=exc.root,
        suggestion=("Check the path is a directory that contains a SwarmKit workspace."),
    )


def _bridge_missing_workspace_file(
    exc: MissingWorkspaceFileError,
) -> ResolutionError:
    return ResolutionError(
        code="workspace.missing-workspace-yaml",
        message=str(exc),
        artifact_path=exc.root,
        suggestion=(
            "Create a workspace.yaml at the workspace root declaring "
            "`apiVersion: swael/v1` and `kind: Workspace`."
        ),
    )


def _bridge_yaml_parse(exc: YAMLParseError) -> ResolutionError:
    return ResolutionError(
        code="workspace.yaml-parse",
        message=str(exc),
        artifact_path=exc.path,
        yaml_pointer=f"/<line {exc.line}>" if exc.line is not None else "",
        suggestion=(
            "Fix the YAML syntax error. Common causes: unclosed brackets, "
            "inconsistent indentation, unescaped special characters in scalars."
        ),
    )


def _bridge_kind_mismatch(exc: ArtifactKindMismatchError) -> ResolutionError:
    return ResolutionError(
        code="workspace.kind-mismatch",
        message=str(exc),
        artifact_path=exc.path,
        yaml_pointer="/kind",
        suggestion=(
            f"Either move the file under the directory that matches "
            f"kind={exc.actual_kind!r}, or change the kind field to "
            f"{exc.expected_kind!r}."
        ),
    )


def _bridge_deep_nesting(exc: DeepNestingError) -> ResolutionError:
    return ResolutionError(
        code="workspace.deep-nesting",
        message=str(exc),
        artifact_path=exc.path,
        suggestion=(
            "Flatten the file to at most one subdirectory level under its category directory."
        ),
    )


def _bridge_malformed(exc: MalformedArtifactError) -> ResolutionError:
    return ResolutionError(
        code="workspace.malformed",
        message=str(exc),
        artifact_path=exc.path,
        suggestion=(
            "The top of a SwarmKit artifact must be a YAML mapping "
            "(key: value pairs), not a list or scalar."
        ),
    )


# Dispatch table. Bridge callables are variadic at the type level so
# concrete-subclass callables (WorkspaceNotFoundError -> ResolutionError,
# etc.) all fit under one tuple type.
_DISCOVERY_BRIDGES: tuple[
    tuple[type[DiscoveryError], Callable[..., ResolutionError]],
    ...,
] = (
    (WorkspaceNotFoundError, _bridge_workspace_not_found),
    (MissingWorkspaceFileError, _bridge_missing_workspace_file),
    (YAMLParseError, _bridge_yaml_parse),
    (ArtifactKindMismatchError, _bridge_kind_mismatch),
    (DeepNestingError, _bridge_deep_nesting),
    (MalformedArtifactError, _bridge_malformed),
)


def resolution_error_from_discovery(exc: DiscoveryError) -> ResolutionError:
    """Convert a :class:`DiscoveryError` subclass into a structured
    :class:`ResolutionError` so ``resolve_workspace()`` can present a
    uniform error surface regardless of which phase failed.
    """
    for exc_type, bridge in _DISCOVERY_BRIDGES:
        if isinstance(exc, exc_type):
            return bridge(exc)
    # Unknown subclass — emit a generic entry so it still surfaces.
    fallback_path = getattr(exc, "path", getattr(exc, "root", _UNKNOWN_PATH))
    return ResolutionError(
        code="workspace.discovery-error",
        message=str(exc),
        artifact_path=fallback_path,
    )


def _to_schema_name(kind: ArtifactKind) -> SchemaName:
    # ArtifactKind and SchemaName are independently declared Literal aliases
    # that share the same five values — mypy recognises the subtype.
    return kind


def _validator_name(exc: ValidationError) -> str:
    # jsonschema exposes ``exc.validator`` as ``str | Unset``. Normalise
    # to an empty string when unset so dict lookups stay typed.
    name = exc.validator
    return name if isinstance(name, str) else ""


def _from_jsonschema(artifact: DiscoveredArtifact, exc: ValidationError) -> ResolutionError:
    code = _VALIDATOR_TO_CODE.get(_validator_name(exc), "schema.other")
    pointer = yaml_pointer(list(exc.absolute_path))
    message = _build_message(artifact.kind, exc)
    rule = _build_rule(exc)
    suggestion = _build_suggestion(exc)
    return ResolutionError(
        code=code,
        message=message,
        artifact_path=artifact.path,
        yaml_pointer=pointer,
        rule=rule,
        suggestion=suggestion,
    )


def _build_message(kind: ArtifactKind, exc: ValidationError) -> str:
    # jsonschema's default message is structurally correct but terse.
    # Prefix with the artifact kind + short pointer context so the
    # message reads well when rendered without additional formatting.
    where = yaml_pointer(list(exc.absolute_path)) or "/"
    first_line = exc.message.splitlines()[0]
    return f"{kind} @ {where}: {first_line}"


def _rule_required(exc: ValidationError) -> str | None:
    missing = _missing_required(exc)
    return f"required: {missing!r}" if missing is not None else None


_RULE_BUILDERS: dict[str, Callable[[ValidationError], str | None]] = {
    "required": _rule_required,
    "enum": lambda exc: f"enum: {exc.validator_value}",
    "const": lambda exc: f"const: {exc.validator_value!r}",
    "pattern": lambda exc: f"pattern: {exc.validator_value!r}",
    "type": lambda exc: f"type: {exc.validator_value!r}",
}


def _build_rule(exc: ValidationError) -> str | None:
    """Human-readable citation of which schema rule was violated."""
    validator = _validator_name(exc)
    builder = _RULE_BUILDERS.get(validator)
    if builder is not None:
        cited = builder(exc)
        if cited is not None:
            return cited
    return validator or None


def _suggest_required(exc: ValidationError) -> str | None:
    missing = _missing_required(exc)
    return f"Add the required field {missing!r}." if missing is not None else None


def _suggest_enum(exc: ValidationError) -> str:
    allowed = exc.validator_value
    if not isinstance(allowed, (list, tuple)):
        return "Use one of the allowed values."
    return f"Use one of: {', '.join(repr(v) for v in allowed)}."


_SUGGESTION_BUILDERS: dict[str, Callable[[ValidationError], str | None]] = {
    "required": _suggest_required,
    "enum": _suggest_enum,
    "pattern": lambda exc: (
        f"Adjust the value to match the required pattern {exc.validator_value!r}."
    ),
    "additionalProperties": lambda _exc: (
        "Remove the unexpected field, or move it into a sub-block that allows free-form keys."
    ),
    "type": lambda exc: f"Change the value to a {exc.validator_value} type.",
    "const": lambda exc: f"Set this field to {exc.validator_value!r}.",
    "minItems": lambda _exc: "Adjust the array length to within the allowed range.",
    "maxItems": lambda _exc: "Adjust the array length to within the allowed range.",
    "minimum": lambda _exc: "Adjust the value to within the allowed range.",
    "maximum": lambda _exc: "Adjust the value to within the allowed range.",
    "minLength": lambda _exc: "Adjust the value to within the allowed range.",
    "maxLength": lambda _exc: "Adjust the value to within the allowed range.",
}


def _build_suggestion(exc: ValidationError) -> str | None:
    """One-line remediation hint based on the failing validator."""
    builder = _SUGGESTION_BUILDERS.get(_validator_name(exc))
    return builder(exc) if builder is not None else None


def _missing_required(exc: ValidationError) -> str | None:
    # jsonschema's `required` error stores the missing field name in
    # ``exc.message`` formatted like ``'<field>' is a required property``.
    # The validator_value is the full list of required fields, which
    # isn't specific enough. Parse the message for the specific field.
    msg = exc.message
    if " is a required property" in msg:
        start = msg.find("'")
        end = msg.find("'", start + 1)
        if 0 <= start < end:
            return msg[start + 1 : end]
    return None


# Sentinel for the unlikely case an unknown DiscoveryError subclass
# has no path attribute. Keeps the return type of
# ``resolution_error_from_discovery`` monomorphic.
_UNKNOWN_PATH = Path("<unknown>")


def errors_or_raise(
    discovery_errors: Sequence[ResolutionError],
    validation_errors: Sequence[ResolutionError],
) -> None:
    """Raise :class:`ResolutionErrors` if either list is non-empty.

    Helper for ``resolve_workspace()``; separated out so the aggregation
    logic is independently testable.
    """
    combined: list[ResolutionError] = [*discovery_errors, *validation_errors]
    if combined:
        raise ResolutionErrors(combined)


def resolve_workspace(root: str | Path) -> ResolvedWorkspace:
    """Load a SwarmKit workspace and produce a fully-resolved, typed tree.

    Runs every phase of the resolver pipeline in order. Collects errors
    without short-circuit and raises :class:`ResolutionErrors` at the
    first phase that surfaces any.

    The returned :class:`ResolvedWorkspace` is frozen and deterministic:
    the same workspace on disk produces byte-identical output (within
    dataclass equality).
    """
    try:
        artifacts = list(discover(root))
    except DiscoveryError as exc:
        raise ResolutionErrors([resolution_error_from_discovery(exc)]) from exc

    validation_errors = validate_discovered(artifacts)
    if validation_errors:
        raise ResolutionErrors(validation_errors)

    # Phase 3 — registries + topologies + triggers, aggregated.
    errors: list[ResolutionError] = []
    skills, skill_errors = build_skill_registry(artifacts)
    errors.extend(skill_errors)
    archetypes, arch_errors = build_archetype_registry(artifacts, skills)
    errors.extend(arch_errors)
    topologies, topo_errors = build_topology_registry(artifacts, skills, archetypes)
    errors.extend(topo_errors)
    triggers, trigger_errors = build_trigger_registry(artifacts, topologies)
    errors.extend(trigger_errors)

    if errors:
        raise ResolutionErrors(errors)

    # Phase 4 — construct the workspace. We already know validation
    # passed; pydantic model construction is a formality, but we still
    # surface a clean error rather than a bare pydantic trace if it ever
    # fails.
    workspace_artifact = next(a for a in artifacts if a.kind == "workspace")
    try:
        workspace_model = SwarmKitWorkspace.model_validate(dict(workspace_artifact.raw))
    except PydanticValidationError as exc:
        raise ResolutionErrors(
            [
                ResolutionError(
                    code="workspace.model-construction",
                    message=(
                        f"workspace at {workspace_artifact.path} could not "
                        "be constructed as a pydantic SwarmKitWorkspace model."
                    ),
                    artifact_path=workspace_artifact.path,
                    suggestion=f"pydantic raised: {exc.errors()[0]['msg']}",
                )
            ]
        ) from exc

    return ResolvedWorkspace(
        raw=workspace_model,
        source_path=workspace_artifact.path,
        topologies=topologies,
        skills=skills,
        archetypes=archetypes,
        triggers=tuple(triggers),
    )


__all__ = [
    "AgentRole",
    "ResolutionError",
    "ResolutionErrors",
    "ResolvedAgent",
    "ResolvedTopology",
    "ResolvedTrigger",
    "ResolvedWorkspace",
    "errors_or_raise",
    "resolution_error_from_discovery",
    "resolve_workspace",
    "validate_discovered",
    "yaml_pointer",
]
