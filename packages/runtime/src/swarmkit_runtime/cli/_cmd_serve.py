"""CLI commands — serve / connect / mcp-serve + the auth-token minter and provider build."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from swarmkit_runtime.auth import AuthProvider

import typer

from ._app import app, auth_app
from ._common import (
    _print_banner,
    _suppress_noisy_logs,
)


@auth_app.command("token")
def auth_token(
    client_id: Annotated[str, typer.Argument(help="Stable id for the caller (appears in audit).")],
    tier: Annotated[str, typer.Option("--tier", help="Scope tier: read | run | admin.")] = "read",
    client_name: Annotated[
        str, typer.Option("--client-name", help="Human-readable name. Defaults to client-id.")
    ] = "",
    env_var: Annotated[
        str, typer.Option("--env-var", help="Env var name to hold the secret.")
    ] = "",
) -> None:
    """Mint a serve API token: generate a strong secret and print the config to wire it.

    Nothing is stored — the secret is shown once. Set the env var, paste the YAML under
    server.auth.config.keys, and restart serve.
    """
    import secrets as _secrets  # noqa: PLC0415

    if tier not in ("read", "run", "admin"):
        typer.echo(f"invalid tier '{tier}' — use read | run | admin", err=True)
        raise typer.Exit(code=2)
    slug = "".join(c if (c.isalnum() or c == "-") else "-" for c in client_id.lower())
    name = env_var or f"{slug.upper().replace('-', '_')}_TOKEN"
    secret = _secrets.token_urlsafe(32)
    cname = client_name or client_id

    typer.echo("# 1. Export the secret (shown once — store it securely):")
    typer.echo(f"export {name}={secret}\n")
    typer.echo("# 2. Add this under server.auth.config.keys in workspace.yaml:")
    typer.echo(
        f"        - key_ref: env:{name}\n"
        f"          client_id: {client_id}\n"
        f"          client_name: {cname}\n"
        f"          tier: {tier}\n"
    )
    typer.echo("# 3. Restart `swarmkit serve` (auth config is read at startup).")
    typer.echo(f"# Callers send:  Authorization: Bearer {secret}")


# ---- auth helper ---------------------------------------------------------


def _auth_requires_secure(workspace_path: Path) -> bool:
    """Read server.auth.require_on_nonloopback (default True)."""
    ws_yaml_path = workspace_path / "workspace.yaml"
    if not ws_yaml_path.exists():
        return True
    import yaml  # noqa: PLC0415

    data = yaml.safe_load(ws_yaml_path.read_text()) or {}
    auth = (data.get("server", {}) or {}).get("auth", {}) or {}
    return bool(auth.get("require_on_nonloopback", True))


def _build_auth_provider(workspace_path: Path) -> AuthProvider:
    """Build auth provider from workspace.yaml ``server.auth`` config."""
    from swarmkit_runtime.auth import (  # noqa: PLC0415
        APIKeyAuthProvider,
        JWTAuthProvider,
        NoneAuthProvider,
    )

    ws_yaml_path = workspace_path / "workspace.yaml"
    if not ws_yaml_path.exists():
        return NoneAuthProvider()

    import yaml  # noqa: PLC0415

    ws_data = yaml.safe_load(ws_yaml_path.read_text()) or {}
    server_config = ws_data.get("server", {}) or {}
    auth_config = server_config.get("auth", {}) or {}
    provider_name = auth_config.get("provider", "none")
    credentials = ws_data.get("credentials", {}) or {}

    if provider_name == "api_key":
        config = auth_config.get("config", {}) or {}
        return APIKeyAuthProvider(keys=config.get("keys", []), credentials=credentials)

    if provider_name == "jwt":
        config = auth_config.get("config", {}) or {}
        return JWTAuthProvider(
            issuer=config["issuer"],
            audience=config.get("audience", "swarmkit"),
            jwks_url=config.get("jwks_url"),
            scopes_claim=config.get("scopes_claim", "scope"),
        )

    # Default: open access
    return NoneAuthProvider()


# ---- stubs for later milestones ------------------------------------------


@app.command()
def serve(
    workspace_path: Annotated[
        Path,
        typer.Argument(help="Workspace root directory.", show_default=False),
    ] = Path("."),
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="Port to listen on."),
    ] = 8000,
    host: Annotated[
        str,
        typer.Option("--host", help="Host to bind to."),
    ] = "0.0.0.0",
    insecure: Annotated[
        bool,
        typer.Option("--insecure", help="Allow a non-loopback bind with no auth (unsafe)."),
    ] = False,
    cors_origin: Annotated[
        list[str] | None,
        typer.Option(
            "--cors-origin",
            help="Exact browser origin allowed to call the API (repeatable). Off by default "
            "(same-origin only); never combined with a wildcard.",
        ),
    ] = None,
) -> None:
    """Start the SwarmKit HTTP server (design §14.1).

    Loads the workspace and exposes topology execution via REST API.
    Endpoints: GET /health, GET /topologies, GET /skills, POST /run/{topology}.

    Default-secure: a non-loopback bind with auth provider 'none' refuses to start unless
    --insecure is given or server.auth.require_on_nonloopback is false.
    """
    _print_banner()
    _suppress_noisy_logs()
    import uvicorn  # noqa: PLC0415

    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    auth_provider = _build_auth_provider(workspace_path.resolve())

    # Default-secure now lives in create_app (so embedders inherit it). The CLI honours
    # server.auth.require_on_nonloopback by folding it into the effective ``insecure`` flag,
    # and maps the factory's refusal to a friendly message + exit code.
    effective_insecure = insecure or not _auth_requires_secure(workspace_path.resolve())
    try:
        app_instance = create_app(
            workspace_path.resolve(),
            auth_provider=auth_provider,
            cors_origins=cors_origin or None,
            host=host,
            insecure=effective_insecure,
        )
    except RuntimeError as exc:
        typer.echo(
            f"{exc}\n(Configure server.auth, bind 127.0.0.1, pass --insecure, or set "
            "server.auth.require_on_nonloopback: false.)",
            err=True,
        )
        raise typer.Exit(code=2) from exc
    uvicorn.run(app_instance, host=host, port=port)


@app.command()
def connect(
    panel_url: Annotated[
        str,
        typer.Argument(help="Control-plane panel base URL.", show_default=False),
    ],
    instance_id: Annotated[
        str,
        typer.Option("--instance-id", help="Instance id from enrollment.", show_default=False),
    ],
    panel_token: Annotated[
        str | None,
        typer.Option("--panel-token", help="Token ref for the panel (env:/file:/literal)."),
    ] = None,
    serve_url: Annotated[
        str,
        typer.Option("--serve-url", help="Local serve base URL (loopback)."),
    ] = "http://127.0.0.1:8000",
    serve_token: Annotated[
        str | None,
        typer.Option("--serve-token", help="Token ref for local serve, if it requires auth."),
    ] = None,
    tier: Annotated[
        str,
        typer.Option("--tier", help="Granted tier (read|run|admin) — re-validated per command."),
    ] = "read",
    interval: Annotated[
        float,
        typer.Option("--interval", help="Seconds between polls."),
    ] = 5.0,
    once: Annotated[
        bool,
        typer.Option("--once", help="Run a single poll cycle and exit (for testing)."),
    ] = False,
) -> None:
    """Run the Mode B poll connector for a NAT'd / edge instance (design §13).

    Reaches the control-plane panel over outbound HTTPS only, drains its per-instance command
    queue, and executes each command against local serve over loopback. No inbound port required.
    """

    from swarmkit_runtime.auth._secrets import resolve_secret_ref  # noqa: PLC0415
    from swarmkit_runtime.connect import run_connector  # noqa: PLC0415

    resolved_panel = resolve_secret_ref(panel_token) if panel_token else None
    resolved_serve = resolve_secret_ref(serve_token) if serve_token else None

    typer.echo(f"connector: polling {panel_url} as instance {instance_id} (tier={tier})")
    asyncio.run(
        run_connector(
            panel_url=panel_url,
            instance_id=instance_id,
            panel_token=resolved_panel,
            serve_url=serve_url,
            serve_token=resolved_serve,
            granted_tier=tier,
            interval=interval,
            once=once,
            log=typer.echo,
        )
    )


@app.command(name="mcp-serve")
def mcp_serve(
    workspace_paths: Annotated[
        list[Path],
        typer.Argument(help="One or more workspace directories to expose."),
    ],
) -> None:
    """Expose workspace topologies as MCP tools on stdio.

    Starts an MCP server that AI assistants (Claude Desktop, Cursor,
    Claude Code) can connect to. Each topology becomes a callable tool.
    """
    _suppress_noisy_logs()
    from swarmkit_runtime.mcp._serve import run_mcp_server  # noqa: PLC0415

    run_mcp_server(workspace_paths)
