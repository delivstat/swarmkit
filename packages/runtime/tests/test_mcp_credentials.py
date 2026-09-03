"""An MCP server is called with the credentials its workspace declared.

`credentials_ref` was in the schema, **dropped at parse time** — it never reached `MCPServerConfig`
at all — and read by nothing on either transport. `_start_http` called `sse_client(url=...)` with no
headers whatsoever.

The part that made it a trap rather than a gap: `env`'s own schema description said

    Use `credentials_ref` for secrets; `env` is for configuration.

So an author following the documentation put their token in a field that was discarded, and found
out at connect time with an auth error naming nothing. The only path that worked was the one the
docs pointed away from.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from swarmkit_runtime.mcp._client import MCPServerConfig, parse_mcp_servers
from swarmkit_runtime.mcp._credentials import (
    CredentialError,
    resolve_env,
    resolve_headers,
    substitute,
)
from swarmkit_schema.models.workspace import SwarmKitWorkspace

CREDS = {"api-token": {"source": "env", "config": {"env": "DEMO_TOKEN"}}}


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_TOKEN", "sk-live-xyz")


class TestHttpSendsABearerToken:
    def test_a_credentials_ref_becomes_an_authorization_header(self) -> None:
        headers = resolve_headers(
            credentials_ref="credentials:api-token", headers=None, credentials=CREDS
        )
        assert headers == {"Authorization": "Bearer sk-live-xyz"}

    def test_no_credentials_ref_sends_no_authorization(self) -> None:
        """An anonymous server must stay anonymous — not gain an empty header."""
        assert resolve_headers(credentials_ref="", headers=None, credentials=CREDS) == {}

    def test_an_explicit_authorization_header_wins(self) -> None:
        """A server whose scheme is not bearer must be able to say so without the derived header
        silently overriding it."""
        headers = resolve_headers(
            credentials_ref="credentials:api-token",
            headers={"Authorization": "Token abc"},
            credentials=CREDS,
        )
        assert headers["Authorization"] == "Token abc"

    def test_extra_headers_are_substituted(self) -> None:
        headers = resolve_headers(
            credentials_ref="", headers={"X-Key": "{credential.api-token}"}, credentials=CREDS
        )
        assert headers["X-Key"] == "sk-live-xyz"

    def test_an_unresolvable_ref_raises_rather_than_sending_nothing(self) -> None:
        """An empty Authorization header is a request that fails for a reason nobody can read."""
        with pytest.raises(CredentialError, match=r"resolved to nothing|not found"):
            resolve_headers(credentials_ref="credentials:absent", headers=None, credentials=CREDS)


class TestStdioGetsItThroughEnv:
    def test_a_credential_placeholder_resolves(self) -> None:
        """Only the author knows which variable the server reads, so they name it."""
        env = resolve_env({"GITHUB_TOKEN": "{credential.api-token}"}, CREDS)
        assert env == {"GITHUB_TOKEN": "sk-live-xyz"}

    def test_plain_env_expansion_still_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PLAIN", "value")
        assert resolve_env({"X": "${PLAIN}"}, CREDS) == {"X": "value"}

    def test_a_literal_value_is_untouched(self) -> None:
        assert resolve_env({"LOG_LEVEL": "debug"}, CREDS) == {"LOG_LEVEL": "debug"}

    def test_an_unresolvable_credential_raises(self) -> None:
        with pytest.raises(CredentialError):
            resolve_env({"X": "{credential.absent}"}, CREDS)


class TestTheConfigCarriesWhatTheSchemaDeclares:
    """The regression that mattered: the value existed in YAML and stopped existing in Python."""

    def _server(self, **kw: object) -> Any:
        class _S:
            def __init__(self, **f: object) -> None:
                self.id = "remote"
                self.transport = type("T", (), {"value": "http"})()
                self.command = None
                self.endpoint = "https://mcp.example.com/mcp"
                self.env = None
                self.cwd = None
                self.sandboxed = None
                self.sandbox_image = None
                self.permission = None
                self.permission_overrides = None
                self.credentials_ref = ""
                self.headers = None
                self.effects = None
                for k, v in f.items():
                    setattr(self, k, v)

        return _S(**kw)

    def test_credentials_ref_survives_parsing(self) -> None:
        cfg = parse_mcp_servers([self._server(credentials_ref="credentials:api-token")], CREDS)
        assert cfg["remote"].credentials_ref == "credentials:api-token"

    def test_headers_survive_parsing(self) -> None:
        cfg = parse_mcp_servers([self._server(headers={"X-Key": "v"})], CREDS)
        assert cfg["remote"].headers == {"X-Key": "v"}

    def test_the_workspace_credentials_block_is_carried_not_resolved(self) -> None:
        """Carried, so the secret is read when a server starts. A workspace declaring a hundred
        servers and starting two holds two secrets, not a hundred."""
        cfg = parse_mcp_servers([self._server(credentials_ref="credentials:api-token")], CREDS)
        assert cfg["remote"].credentials == CREDS
        assert "sk-live-xyz" not in str(cfg["remote"].__dict__)

    def test_a_config_with_no_credentials_still_parses(self) -> None:
        cfg = parse_mcp_servers([self._server()], None)
        assert cfg["remote"].credentials_ref == ""
        assert cfg["remote"].credentials == {}


class TestSubstitutionRules:
    @pytest.mark.parametrize(
        ("ref", "expected"),
        [
            ("{credential.api-token}", "sk-live-xyz"),
            ("{credential.credentials:api-token}", "sk-live-xyz"),
        ],
    )
    def test_a_bare_name_and_a_prefixed_ref_both_resolve(self, ref: str, expected: str) -> None:
        """A bare name means the workspace `credentials` block; a prefixed one is passed through."""
        assert substitute(ref, CREDS) == expected

    def test_a_secret_is_not_logged_by_the_error_path(self) -> None:
        with pytest.raises(CredentialError) as exc:
            substitute("{credential.absent}", CREDS)
        assert "sk-live-xyz" not in str(exc.value)


class TestTheDefaultConfigIsUnchanged:
    def test_a_bare_config_sends_nothing(self) -> None:
        cfg = MCPServerConfig(server_id="x")
        assert cfg.credentials_ref == ""
        assert cfg.headers == {}
        assert (
            resolve_headers(
                credentials_ref=cfg.credentials_ref,
                headers=cfg.headers,
                credentials=cfg.credentials,
            )
            == {}
        )


class TestTheRealWorkspacePath:
    """Through `SwarmKitWorkspace`, not a hand-built dict.

    The tests above build `{"source": "env"}` as a **string**, which is what a person writes in
    YAML — but pydantic parses it to `<Source.env: 'env'>`, and `resolve_secret_ref` compares
    against strings. So every credential failed with `unknown source 'Source.env'` while the unit
    tests passed, because they had already made the assumption the code got wrong.

    A test that builds its own input can only confirm what its author believed. This one starts
    from YAML.
    """

    WORKSPACE = """
apiVersion: swarmkit/v1
kind: Workspace
metadata: { id: t, name: t }
credentials:
  remote-token:
    source: env
    config: { env: DEMO_TOKEN }
mcp_servers:
  - id: remote
    transport: http
    endpoint: https://mcp.example.com/mcp
    credentials_ref: credentials:remote-token
"""

    def _credentials(self) -> tuple[dict[str, Any], Any]:
        ws = SwarmKitWorkspace.model_validate(yaml.safe_load(self.WORKSPACE))
        return {
            k: (v if isinstance(v, dict) else v.model_dump(mode="json", exclude_none=True))
            for k, v in dict(ws.credentials or {}).items()
        }, ws

    def test_a_yaml_declared_credential_reaches_the_server(self) -> None:
        creds, ws = self._credentials()
        cfg = parse_mcp_servers(ws.mcp_servers, creds)["remote"]
        headers = resolve_headers(
            credentials_ref=cfg.credentials_ref, headers=cfg.headers, credentials=cfg.credentials
        )
        assert headers == {"Authorization": "Bearer sk-live-xyz"}

    def test_the_source_survives_as_a_string(self) -> None:
        """The specific regression: `model_dump()` keeps the enum, `mode="json"` does not."""
        creds, _ = self._credentials()
        assert creds["remote-token"]["source"] == "env"
        assert not str(creds["remote-token"]["source"]).startswith("Source.")

    def test_this_is_the_cli_only_path(self) -> None:
        """No portal involved: a token exported in a shell, named by a YAML credential entry,
        reaching a remote MCP server. The browser flow adds to this; it is not a prerequisite."""
        creds, ws = self._credentials()
        cfg = parse_mcp_servers(ws.mcp_servers, creds)["remote"]
        assert cfg.endpoint.startswith("https://")
        assert resolve_headers(
            credentials_ref=cfg.credentials_ref, headers=cfg.headers, credentials=cfg.credentials
        )["Authorization"].endswith("sk-live-xyz")
