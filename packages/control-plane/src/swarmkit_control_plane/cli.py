"""CLI for the SwarmKit control plane."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="SwarmKit control plane — fleet panel API + instance registry.")

_CORS_ENV = "SWARMKIT_CONTROL_PLANE_CORS_ORIGINS"
_OPERATOR_TOKENS_ENV = "SWARMKIT_CONTROL_PLANE_OPERATOR_TOKENS"
_OIDC_ISSUER_ENV = "SWARMKIT_CONTROL_PLANE_OIDC_ISSUER"


@app.command()
def serve(
    data_dir: Annotated[
        Path, typer.Option("--data-dir", help="Where to store the registry sqlite.")
    ] = Path(".swarmkit-control-plane"),
    host: Annotated[str, typer.Option("--host", help="Host to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Port.")] = 8800,
    cors_origin: Annotated[
        list[str] | None,
        typer.Option(
            "--cors-origin",
            help=(
                "Browser origin allowed to call the panel (repeatable). CORS is config-only — "
                "pass the fleet UI's origin (e.g. its dev URL). Also read from "
                f"${_CORS_ENV} (comma-separated)."
            ),
        ),
    ] = None,
    operator_token: Annotated[
        list[str] | None,
        typer.Option(
            "--operator-token",
            help=(
                "Operator bearer token granting full panel access (repeatable). When set, the "
                "panel requires auth on every route except /health; otherwise it runs open. Also "
                f"read from ${_OPERATOR_TOKENS_ENV} (comma-separated)."
            ),
        ),
    ] = None,
    oidc_issuer: Annotated[
        str | None,
        typer.Option(
            "--oidc-issuer",
            help="OIDC issuer URL. When set, valid JWTs from it authenticate as operators.",
        ),
    ] = None,
    oidc_audience: Annotated[
        str,
        typer.Option("--oidc-audience", help="Expected `aud` claim on OIDC tokens."),
    ] = "swarmkit-control-plane",
    oidc_jwks_url: Annotated[
        str | None,
        typer.Option("--oidc-jwks-url", help="JWKS URL (defaults to the issuer's discovery path)."),
    ] = None,
    collector_endpoint: Annotated[
        str,
        typer.Option(
            "--collector-endpoint", help="OTLP collector endpoint to advertise to instances."
        ),
    ] = "",
    jaeger_url: Annotated[
        str,
        typer.Option(
            "--jaeger-url", help="Jaeger UI base URL — the fleet UI deep-links traces here."
        ),
    ] = "",
    grafana_url: Annotated[
        str,
        typer.Option(
            "--grafana-url", help="Grafana base URL — the fleet UI deep-links metrics here."
        ),
    ] = "",
) -> None:
    """Start the control-plane API."""
    import uvicorn  # noqa: PLC0415

    from swarmkit_control_plane._app import create_app  # noqa: PLC0415
    from swarmkit_control_plane._oidc import OidcVerifier  # noqa: PLC0415
    from swarmkit_control_plane._registry import SqliteRegistry  # noqa: PLC0415

    origins = list(cors_origin or [])
    origins += [o.strip() for o in os.environ.get(_CORS_ENV, "").split(",") if o.strip()]

    op_tokens = list(operator_token or [])
    op_tokens += [
        t.strip() for t in os.environ.get(_OPERATOR_TOKENS_ENV, "").split(",") if t.strip()
    ]

    issuer = oidc_issuer or os.environ.get(_OIDC_ISSUER_ENV, "").strip()
    oidc = (
        OidcVerifier(issuer=issuer, audience=oidc_audience, jwks_url=oidc_jwks_url)
        if issuer
        else None
    )

    registry = SqliteRegistry(data_dir / "registry.sqlite")
    if not op_tokens and oidc is None:
        typer.echo(
            "warning: no operator tokens or OIDC issuer set — panel API is UNAUTHENTICATED. "
            f"Set --operator-token / ${_OPERATOR_TOKENS_ENV} or --oidc-issuer to require auth.",
            err=True,
        )
    observability = {
        "collector_endpoint": collector_endpoint
        or os.environ.get("SWARMKIT_CONTROL_PLANE_COLLECTOR_ENDPOINT", ""),
        "jaeger_url": jaeger_url or os.environ.get("SWARMKIT_CONTROL_PLANE_JAEGER_URL", ""),
        "grafana_url": grafana_url or os.environ.get("SWARMKIT_CONTROL_PLANE_GRAFANA_URL", ""),
    }

    uvicorn.run(
        create_app(
            registry,
            cors_origins=origins,
            operator_tokens=op_tokens,
            oidc=oidc,
            observability=observability,
        ),
        host=host,
        port=port,
    )


if __name__ == "__main__":
    app()
