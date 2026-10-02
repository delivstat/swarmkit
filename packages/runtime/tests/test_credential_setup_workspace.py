"""The credential-setup reference workspace resolves and compiles.

See `design/details/google-workspace-setup.md`. The runtime side of the "setup is a swarm,
not a docs page" story. This test keeps the YAML honest — a stray field or a bad reference
in the archetypes / topology / skill files breaks the resolve, not a user running the swarm.
"""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.resolver import resolve_workspace

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE = REPO_ROOT / "reference" / "workspaces" / "credential-setup"
REGISTER_SCRIPT = (
    WORKSPACE / "command_packs" / "credential-setup-tools" / "register_oauth_client.py"
)


def test_workspace_resolves_and_declares_the_setup_topology() -> None:
    ws = resolve_workspace(WORKSPACE)
    assert "google-workspace-setup" in ws.topologies
    # Both archetypes referenced by the topology must be present.
    assert "google-workspace-concierge" in ws.archetypes
    assert "oauth-client-registrar" in ws.archetypes
    # The command skill the registrar relies on resolves against the pack entry.
    assert "register-oauth-client" in ws.skills


def test_setup_topology_compiles() -> None:
    """A compile error here would mean a stray field, bad archetype ref, or broken skill
    binding in the YAML. The topology just has to *construct* — we're not executing it."""
    runtime = WorkspaceRuntime.from_workspace_path(WORKSPACE)
    compiled = runtime.compile("google-workspace-setup")
    assert compiled is not None


# ---- the registrar helper script ------------------------------------------------------------

PLAN = {
    "issuer": "https://accounts.google.com",
    "client_id": "123.apps.googleusercontent.com",
    "client_secret": "GOCSPX-redacted",
    "client_type": "desktop",
    "display_name": "Google (test)",
    "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
}


def _run_script(stdin: str, env: dict[str, str]) -> tuple[int, str, str]:
    """Run the script in a subprocess so stdin/stdout/exit-code reflect real behaviour."""
    proc = subprocess.run(
        [sys.executable, str(REGISTER_SCRIPT)],
        input=stdin,
        env={**env, "PYTHONPATH": str(REPO_ROOT / "packages" / "runtime" / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_register_script_rejects_missing_client_id() -> None:
    bad = {k: v for k, v in PLAN.items() if k != "client_id"}
    code, _, stderr = _run_script(json.dumps(bad), env={})
    assert code == 2
    assert "client_id" in stderr


def test_register_script_rejects_missing_issuer_and_endpoint() -> None:
    bad = {k: v for k, v in PLAN.items() if k != "issuer"}
    code, _, stderr = _run_script(json.dumps(bad), env={})
    assert code == 2
    assert "issuer or endpoint" in stderr


class _MockOAuthClientsHandler(http.server.BaseHTTPRequestHandler):
    received: ClassVar[dict[str, Any]] = {}

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        _MockOAuthClientsHandler.received["path"] = urlparse(self.path).path
        _MockOAuthClientsHandler.received["body"] = json.loads(body)
        _MockOAuthClientsHandler.received["auth"] = self.headers.get("Authorization", "")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "issuer": PLAN["issuer"],
                    "client_id": PLAN["client_id"],
                    "client_type": PLAN["client_type"],
                    "display_name": PLAN["display_name"],
                    "scopes": PLAN["scopes"],
                    "created_at": 1,
                }
            ).encode("utf-8")
        )

    def log_message(self, *_: object) -> None:  # keep test output clean
        return


def test_register_script_posts_to_the_runtime_and_prints_body() -> None:
    """Point the script at a tiny HTTP mock and verify the request shape."""
    _MockOAuthClientsHandler.received = {}
    server = http.server.HTTPServer(("127.0.0.1", 0), _MockOAuthClientsHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        host, port = server.server_address
        code, stdout, stderr = _run_script(
            json.dumps(PLAN),
            env={
                "SWARMKIT_RUNTIME_URL": f"http://{host}:{port}",
                "SWARMKIT_RUNTIME_TOKEN": "test-token",
                "PATH": os.environ.get("PATH", ""),
            },
        )
    finally:
        server.shutdown()

    assert code == 0, stderr
    assert _MockOAuthClientsHandler.received["path"] == "/api/oauth/clients"
    assert _MockOAuthClientsHandler.received["auth"] == "Bearer test-token"
    assert _MockOAuthClientsHandler.received["body"] == PLAN
    # Stdout is the display row the runtime returned.
    assert json.loads(stdout)["issuer"] == PLAN["issuer"]
    assert "GOCSPX" not in stdout  # secret must not round-trip to stdout
