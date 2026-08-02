"""The environment variables the runtime actually reads, and what each one does.

A curated registry, deliberately **not** a dump of ``os.environ``. The report it produces is served
over a read-scope HTTP endpoint and printed into terminal scrollback, and a process running
SwarmKit has ``ANTHROPIC_API_KEY`` and a database password sitting in its environment. Listing only
known keys — and masking the ones whose values are secret — is the difference between an
infrastructure page and a credential leak.

It exists because environment variables are invisible config: a run behaves differently on one
machine, nothing in the workspace explains why, and the operator has no list of what to even check.
``SWARMKIT_STORE_URL`` being set-but-ignored is exactly that failure (storage-service.md).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Values that are never shown, only reported as set/unset. Anything carrying a credential.
SECRET = "secret"
#: Values shown with any userinfo masked — connection strings routinely embed a password.
URLISH = "url"
#: Values shown as-is.
PLAIN = "plain"


@dataclass(frozen=True)
class EnvVar:
    name: str
    group: str
    description: str
    kind: str = PLAIN


#: Grouped by the question an operator is asking when they come looking.
REGISTRY: tuple[EnvVar, ...] = (
    # ---- where data goes ----
    EnvVar(
        "SWARMKIT_STORE_URL",
        "Storage",
        "Connection URL for every store. Set alone it also SELECTS postgres — a URL names its own "
        "backend. Overrides storage.runtime.url in workspace.yaml.",
        URLISH,
    ),
    EnvVar(
        "SWARMKIT_STORE_BACKEND",
        "Storage",
        "Force the backend (sqlite | postgres) regardless of workspace.yaml. Optional: setting "
        "only the URL is enough.",
    ),
    EnvVar(
        "DATABASE_URL",
        "Storage",
        "Fallback connection URL when SWARMKIT_STORE_URL is unset.",
        URLISH,
    ),
    EnvVar("SWARMKIT_WORKSPACE", "Storage", "Default workspace root for commands that omit it."),
    EnvVar("SWARMKIT_GATES_DIR", "Storage", "Where file-backed approval gates are written."),
    # ---- which model runs ----
    EnvVar("SWARMKIT_PROVIDER", "Models", "Default model provider when a topology names none."),
    EnvVar("SWARMKIT_MODEL", "Models", "Default model when a topology names none."),
    EnvVar("SWARMKIT_JUDGE_MODEL", "Models", "Model used by governance decision skills."),
    EnvVar("SWARMKIT_AUTHOR_MODEL", "Models", "Model used by the authoring swarms."),
    EnvVar("SWARMKIT_MODEL_TIMEOUT", "Models", "Per-call timeout in seconds."),
    EnvVar("SWARMKIT_MODEL_RETRIES", "Models", "Retries per model call before the node fails."),
    EnvVar("ANTHROPIC_API_KEY", "Models", "Anthropic credential.", SECRET),
    EnvVar("OPENAI_API_KEY", "Models", "OpenAI credential.", SECRET),
    EnvVar("OPENROUTER_API_KEY", "Models", "OpenRouter credential.", SECRET),
    EnvVar("GOOGLE_API_KEY", "Models", "Google GenAI credential.", SECRET),
    # ---- how much a run may do ----
    EnvVar("SWARMKIT_MAX_TOOL_TURNS", "Run limits", "Tool-calling turns before a node is cut off."),
    EnvVar("SWARMKIT_MAX_TOOLS", "Run limits", "Tools exposed to one agent."),
    EnvVar("SWARMKIT_MAX_RESULT_CHARS", "Run limits", "Truncation ceiling for a tool result."),
    EnvVar("SWARMKIT_MAX_DELEGATIONS_PER_CHILD", "Run limits", "Delegation fan-out cap per child."),
    EnvVar("SWARMKIT_AGENT_RETRIES", "Run limits", "Retries for a failing agent node."),
    EnvVar("SWARMKIT_HISTORY_TURNS", "Run limits", "Conversation turns replayed into context."),
    EnvVar(
        "SWARMKIT_CONTEXT_COMPRESSION",
        "Run limits",
        "Enable read-side context compression (off by default).",
    ),
    EnvVar(
        "SWARMKIT_CONTEXT_COMPRESSION_MIN_BYTES",
        "Run limits",
        "Payload size below which compression is skipped.",
    ),
    # ---- MCP + sandboxing ----
    EnvVar("SWARMKIT_MCP_TIMEOUT", "MCP + sandbox", "Per-call MCP timeout in seconds."),
    EnvVar("SWARMKIT_MCP_RETRIES", "MCP + sandbox", "Retries for a failing MCP call."),
    EnvVar("SWARMKIT_CONTAINER_RUNTIME", "MCP + sandbox", "docker | podman for sandboxed servers."),
    EnvVar("SWARMKIT_SANDBOX_IMAGE", "MCP + sandbox", "Image used to sandbox an MCP server."),
    EnvVar("SWARMKIT_HARNESS_IMAGE", "MCP + sandbox", "Image used to run a harness executor."),
    EnvVar(
        "SWARMKIT_DISABLE_CONTAINER_SANDBOX",
        "MCP + sandbox",
        "Run MCP servers on the host instead of in a container. Weakens isolation.",
    ),
    EnvVar(
        "SWARMKIT_DOCS_READER_ALLOW_OUTSIDE",
        "MCP + sandbox",
        "Let docs-reader read outside its workspace root. Disables path confinement.",
    ),
    # ---- fleet + telemetry ----
    EnvVar(
        "SWARMKIT_FLEET_REQUIRE_IDENTITY",
        "Fleet",
        "Reject fleet calls that do not present a pinned identity.",
    ),
    EnvVar(
        "SWARMKIT_FLEET_REQUIRE_SIGNED_DEPLOY",
        "Fleet",
        "Reject unsigned artifact deploys from a fleet.",
    ),
    EnvVar(
        "SWARMKIT_OTEL_EXPORTER", "Telemetry", "OTLP exporter (otlp | console). Off when unset."
    ),
    EnvVar("SWARMKIT_OTEL_ENDPOINT", "Telemetry", "OTLP collector endpoint.", URLISH),
    EnvVar("SWARMKIT_OTEL_HEADERS", "Telemetry", "Extra OTLP headers.", SECRET),
    EnvVar("SWARMKIT_OTEL_API_KEY", "Telemetry", "OTLP collector credential.", SECRET),
    # ---- output ----
    EnvVar("SWARMKIT_ENV", "Output", "Deployment label reported by serve."),
    EnvVar("SWARMKIT_VERBOSE", "Output", "Verbose CLI output."),
    EnvVar("SWARMKIT_QUIET", "Output", "Suppress non-essential CLI output."),
)


def _display(var: EnvVar, value: str) -> str:
    if var.kind == SECRET:
        return "set"
    if var.kind == URLISH:
        from swarmkit_runtime.persistence._store import redacted_url  # noqa: PLC0415

        return redacted_url(value)
    return value


#: Fallback only. The workspace SHOULD declare its secrets explicitly (the reserved `secrets:` list
#: in workspace.env.yaml); these fragments catch the common cases when it has not, because a
#: workspace written before that existed should not start leaking on upgrade. A heuristic is a
#: guess, and it misses `db.dsn` — which is exactly why the declaration is the documented path.
_SECRET_HINTS = ("key", "token", "secret", "password", "passwd", "credential")


def _looks_secret(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


def workspace_properties(workspace_root: Path) -> list[dict[str, object]]:
    """The workspace's own parameters, read from ``workspace.env.yaml``.

    See design/details/workspace-env-config.md.

    Data-sourced on purpose. A hand-maintained list drifts the moment a feature adds a parameter,
    so this reads the file every time: whatever the workspace declares shows up here, with no code
    change and no registry entry. :data:`REGISTRY` stays hand-written because it documents what the
    RUNTIME reads — names that exist in code, not in any file, and that need a description a config
    file cannot supply.

    Values are resolved (``${ENV_VAR}`` already substituted), so this shows what the run actually
    used rather than what was typed. A value is masked when the workspace lists its path under the
    reserved ``secrets:`` key — or, failing that, when its name looks like a credential.
    """
    from swarmkit_runtime.resolver._env_config import (  # noqa: PLC0415
        load_env_config,
        load_secret_paths,
    )

    declared = load_secret_paths(workspace_root)
    rows: list[dict[str, object]] = []
    for key, value in sorted(load_env_config(workspace_root).items()):
        # Declared OR guessed. A declaration can add to the masked set; it cannot remove from it,
        # so `secrets: []` in a file full of api keys does not un-mask them.
        secret = key in declared or _looks_secret(key)
        rows.append(
            {
                "name": key,
                "value": "set" if secret else _mask_if_url(value),
                "sensitive": secret,
            }
        )
    return rows


def _mask_if_url(value: str) -> str:
    if "://" not in value:
        return value
    from swarmkit_runtime.persistence._store import redacted_url  # noqa: PLC0415

    return redacted_url(value)


def environment_report(*, include_unset: bool = True) -> list[dict[str, object]]:
    """Every known variable: whether it is set, its (masked) value, and what it does.

    Unset entries are included by default — the list of what you *could* set is most of the value
    when the question is "why is this behaving differently here".
    """
    rows: list[dict[str, object]] = []
    for var in REGISTRY:
        raw = os.environ.get(var.name)
        if raw is None and not include_unset:
            continue
        rows.append(
            {
                "name": var.name,
                "group": var.group,
                "description": var.description,
                "set": raw is not None,
                "value": _display(var, raw) if raw is not None else None,
                "sensitive": var.kind == SECRET,
            }
        )
    return rows


__all__ = [
    "PLAIN",
    "REGISTRY",
    "SECRET",
    "URLISH",
    "EnvVar",
    "environment_report",
    "workspace_properties",
]
