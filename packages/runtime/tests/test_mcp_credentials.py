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
from swarmkit_runtime.mcp._client import MCPServerConfig, parse_mcp_servers
from swarmkit_runtime.mcp._credentials import (
    CredentialError,
    resolve_env,
    resolve_headers,
    substitute,
)

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
