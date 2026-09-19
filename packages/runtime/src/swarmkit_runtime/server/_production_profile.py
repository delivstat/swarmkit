"""The ``--profile production`` fail-closed preflight (production-profile.md).

Collects every way the deployment is not production-safe and returns them together, so
``create_app`` can refuse to start with a complete list rather than one gap at a time. A deployment
assertion, not a request-time gate — governance is already deny-by-default at the policy engine.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from swarmkit_runtime.auth import AuthProvider, NoneAuthProvider

_OAUTH_KEY_ENV = "SWARMKIT_OAUTH_KEY"


def _declared_mcp_servers(workspace_path: Path) -> list[dict[str, Any]]:
    """The workspace's ``mcp_servers`` entries as plain dicts, or [] when there are none or the file
    cannot be read (a resolve error surfaces elsewhere; the preflight does not double-report)."""
    ws_yaml = workspace_path / "workspace.yaml"
    if not ws_yaml.exists():
        return []
    try:
        import yaml  # noqa: PLC0415

        data = yaml.safe_load(ws_yaml.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    servers = data.get("mcp_servers") or []
    return [s for s in servers if isinstance(s, dict)]


def production_violations(
    workspace_path: Path,
    *,
    auth: AuthProvider,
    insecure: bool,
    cors_origins: list[str] | None,
) -> list[str]:
    """Every fail-closed requirement the deployment does not meet (empty = production-ready)."""
    violations: list[str] = []

    if isinstance(auth, NoneAuthProvider):
        violations.append(
            "auth provider is 'none' — configure server.auth (api_key or jwt); "
            "production does not serve anonymous, on any bind"
        )
    if insecure:
        violations.append("--insecure is set — it is incompatible with --profile production")
    if not os.environ.get(_OAUTH_KEY_ENV):
        violations.append(
            f"{_OAUTH_KEY_ENV} is not set — the OAuth token-encryption key would regenerate on "
            "restart and silently invalidate every stored token; set a persistent key"
        )
    if cors_origins and any(o.strip() == "*" for o in cors_origins):
        violations.append("a wildcard CORS origin ('*') is configured — list exact origins only")
    for server in _declared_mcp_servers(workspace_path):
        if not server.get("sandboxed", False):
            name = server.get("id") or server.get("name") or "?"
            violations.append(
                f"MCP server '{name}' is not sandboxed — set `sandboxed: true` "
                "so the tool process runs isolated (design §8.8)"
            )
    return violations


def assert_production_ready(
    workspace_path: Path,
    *,
    auth: AuthProvider,
    insecure: bool,
    cors_origins: list[str] | None,
) -> None:
    """Raise ``RuntimeError`` listing every gap when the deployment is not fail-closed."""
    gaps = production_violations(
        workspace_path, auth=auth, insecure=insecure, cors_origins=cors_origins
    )
    if gaps:
        numbered = "\n".join(f"  {i}. {g}" for i, g in enumerate(gaps, 1))
        raise RuntimeError(
            "--profile production refuses to start: the deployment is not fail-closed.\n"
            f"{numbered}\n"
            "Fix these, or run without --profile production for the permissive default."
        )
