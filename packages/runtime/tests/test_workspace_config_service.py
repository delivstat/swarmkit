"""The portal can edit workspace infrastructure without vandalising the file or leaking a secret.

Two properties carry this feature, and both are easy to lose silently:

* `workspace.yaml` is hand-edited and committed, so a form save must change the field the form
  changed and nothing else. A library that parses to dicts and re-emits would pass every functional
  test here while reformatting somebody's workspace and dropping the comment explaining why a
  server is `cautious`.
* A credential value must never reach the browser. "Show the token so the user can check it" is a
  reasonable-sounding request that puts a secret in devtools, history and every installed extension.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from swarmkit_runtime.server import create_app
from swarmkit_runtime.server._services import NotFoundError
from swarmkit_runtime.server._workspace_config import ConfigError, WorkspaceConfigService

WORKSPACE = """\
apiVersion: swarmkit/v1
kind: Workspace
metadata:
  id: cfg-demo
  name: Config Demo
governance:
  provider: mock
  policy_language: yaml
credentials:
  # The bot token. Never literal — this names an environment variable.
  telegram-bot-token:
    source: env
    config:
      env: TELEGRAM_BOT_TOKEN
mcp_servers:
  # readonly because reading a repo cannot change one.
  - id: git
    transport: stdio
    command: ["uvx", "mcp-server-git", "--repository", "."]
    permission: readonly
    effects:
      git_status: read
channels:
  ops:
    provider: telegram
    credentials_ref: telegram-bot-token
    config:
      chat_id: '-100999'
"""


@pytest.fixture
def svc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WorkspaceConfigService:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token-value")
    (tmp_path / "workspace.yaml").write_text(WORKSPACE)
    return WorkspaceConfigService(tmp_path)


# ---- reading ---------------------------------------------------------------------------------


def test_read_reports_shape_and_never_the_secret(svc: WorkspaceConfigService) -> None:
    out = svc.read()
    cred = out["credentials"][0]
    assert cred["id"] == "telegram-bot-token"
    assert cred["source"] == "env"
    assert cred["config"] == {"env": "TELEGRAM_BOT_TOKEN"}
    assert "secret-token-value" not in str(out)


def test_read_says_whether_a_credential_actually_resolves(
    svc: WorkspaceConfigService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The answer a setup screen needs. An env var nobody exported looks identical to a working
    credential in every other view, and surfaces much later as a platform auth error."""
    assert svc.read()["credentials"][0]["resolves"] is True
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    assert svc.read()["credentials"][0]["resolves"] is False


def test_read_lists_servers_and_channels(svc: WorkspaceConfigService) -> None:
    out = svc.read()
    assert [s["id"] for s in out["mcp_servers"]] == ["git"]
    assert out["channels"][0]["id"] == "ops"


# ---- the property that protects the user's file ----------------------------------------------


def test_saving_preserves_comments_and_untouched_formatting(svc: WorkspaceConfigService) -> None:
    """The diff after a form save should be the field that changed, not the whole file."""
    before = (svc.workspace_path / "workspace.yaml").read_text()
    result = svc.upsert(
        "channels",
        "eng",
        {"provider": "discord", "credentials_ref": "telegram-bot-token"},
    )
    assert result["saved"] is True
    after = (svc.workspace_path / "workspace.yaml").read_text()

    assert "# The bot token. Never literal" in after
    assert "# readonly because reading a repo cannot change one." in after
    # Everything that was there before is still there, in order.
    for line in before.splitlines():
        assert line in after


def test_upsert_replaces_a_list_entry_by_id(svc: WorkspaceConfigService) -> None:
    svc.upsert("mcp_servers", "git", {"transport": "stdio", "command": ["true"]})
    servers = svc.read()["mcp_servers"]
    assert len(servers) == 1
    assert servers[0]["command"] == ["true"]


# ---- a bad write must not survive -------------------------------------------------------------


def test_a_write_that_would_not_load_is_rolled_back(svc: WorkspaceConfigService) -> None:
    """A half-written workspace left behind by a rejected form is worse than a rejected form."""
    original = (svc.workspace_path / "workspace.yaml").read_text()
    result = svc.upsert("channels", "bad", {"provider": "discord", "inbound": True})
    assert result["saved"] is False
    assert result["errors"]
    assert (svc.workspace_path / "workspace.yaml").read_text() == original


def test_a_section_the_portal_may_not_edit_is_refused(svc: WorkspaceConfigService) -> None:
    """`governance` and `storage` change how the runtime is governed. A form is the wrong
    instrument for a decision that wants a review."""
    with pytest.raises(ConfigError, match="not portal-editable"):
        svc.upsert("governance", "provider", {"provider": "allow_all"})


# ---- deletion --------------------------------------------------------------------------------


def test_deleting_a_referenced_credential_is_refused_with_the_users_named(
    svc: WorkspaceConfigService,
) -> None:
    """Deleting it would leave a workspace that resolves until something tries to authenticate —
    the failure would surface in a run, far from the click that caused it."""
    with pytest.raises(ConfigError, match="channels/ops"):
        svc.delete("credentials", "telegram-bot-token")


def test_deleting_an_unreferenced_entry_works(svc: WorkspaceConfigService) -> None:
    svc.upsert("credentials", "spare", {"source": "env", "config": {"env": "NOPE"}})
    assert svc.delete("credentials", "spare")["saved"] is True
    assert [c["id"] for c in svc.read()["credentials"]] == ["telegram-bot-token"]


def test_deleting_something_absent_is_a_not_found(svc: WorkspaceConfigService) -> None:
    with pytest.raises(NotFoundError):
        svc.delete("mcp_servers", "nope")


def test_the_result_still_parses_as_yaml(svc: WorkspaceConfigService) -> None:
    svc.upsert("channels", "eng", {"provider": "slack", "credentials_ref": "telegram-bot-token"})
    doc = yaml.safe_load((svc.workspace_path / "workspace.yaml").read_text())
    assert set(doc["channels"]) == {"ops", "eng"}


# ---- the HTTP surface -------------------------------------------------------------------------


@pytest.fixture
def client(svc: WorkspaceConfigService, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]

    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    with TestClient(create_app(svc.workspace_path)) as c:
        yield c


def test_get_config_endpoint_redacts(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/workspace/config")
    assert resp.status_code == 200
    assert "secret-token-value" not in resp.text
    assert resp.json()["credentials"][0]["resolves"] is True


def test_put_then_delete_round_trip(client) -> None:  # type: ignore[no-untyped-def]
    put = client.put(
        "/api/workspace/config/channels/eng",
        json={"provider": "discord", "credentials_ref": "telegram-bot-token"},
    )
    assert put.json()["saved"] is True
    assert {c["id"] for c in client.get("/api/workspace/config").json()["channels"]} == {
        "ops",
        "eng",
    }

    gone = client.delete("/api/workspace/config/channels/eng")
    assert gone.json()["saved"] is True


def test_put_a_forbidden_section_is_a_400(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.put("/api/workspace/config/governance/provider", json={"provider": "allow_all"})
    assert resp.status_code == 400
