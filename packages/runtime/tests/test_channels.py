"""Channel skills — send, ask, and the wiring whose absence orphaned the transports.

The notification providers shipped complete, tested and unreachable: 31 unit tests proved each
provider *worked* and nothing proved one ever *ran*. So the first test here is deliberately the
boring one — a workspace `channels:` block reaching a constructed provider — because that is the
assertion whose absence was the whole bug.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import swarmkit_runtime.channels._server as srv
import yaml
from swarmkit_runtime.channels import ChannelConfigError, load_channels
from swarmkit_runtime.notifications import TelegramNotificationProvider

CREDENTIALS: dict[str, Any] = {
    "tg": {"source": "env", "config": {"env": "TEST_TG_TOKEN"}},
    "dc": {"source": "env", "config": {"env": "TEST_DC_HOOK"}},
}

WS: dict[str, Any] = {
    "apiVersion": "swarmkit/v1",
    "kind": "Workspace",
    "metadata": {"id": "t", "name": "T"},
    "credentials": CREDENTIALS,
    "channels": {
        "ops": {
            "provider": "telegram",
            "credentials_ref": "tg",
            "inbound": True,
            "config": {"chat_id": "-100999"},
        },
        "eng": {"provider": "discord", "credentials_ref": "dc"},
    },
}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TEST_TG_TOKEN", "bot-token-123")
    monkeypatch.setenv("TEST_DC_HOOK", "https://discord.example/hook")
    (tmp_path / "workspace.yaml").write_text(yaml.safe_dump(WS))
    return tmp_path


# ---- the regression that matters ---------------------------------------------------------


def test_channels_block_reaches_a_constructed_provider(workspace: Path) -> None:
    """A declared channel becomes a live provider.

    This is the check whose absence let 548 lines of transport ship unreachable. If the wiring is
    removed, this fails — which is the only reason the earlier code could not have shipped broken.
    """
    channels = load_channels(workspace)
    assert set(channels) == {"ops", "eng"}
    assert isinstance(channels["ops"].provider, TelegramNotificationProvider)
    assert channels["ops"].secret == "bot-token-123"


def test_no_channels_block_is_not_an_error(tmp_path: Path) -> None:
    (tmp_path / "workspace.yaml").write_text(yaml.safe_dump({"apiVersion": "swarmkit/v1"}))
    assert load_channels(tmp_path) == {}


# ---- configuration refuses rather than degrades ------------------------------------------


def test_missing_credential_entry_names_the_ref(tmp_path: Path) -> None:
    doc = {**WS, "credentials": {}}
    (tmp_path / "workspace.yaml").write_text(yaml.safe_dump(doc))
    # Either channel may be reached first — safe_dump sorts keys — so assert the shape of the
    # message rather than which one lost the race.
    with pytest.raises(ChannelConfigError, match=r"references credential '(tg|dc)'"):
        load_channels(tmp_path)


def test_unresolvable_credential_fails_at_load_not_at_send(tmp_path: Path) -> None:
    """An empty token would fail later as a platform auth error naming Telegram, not the secret."""
    (tmp_path / "workspace.yaml").write_text(yaml.safe_dump(WS))
    with pytest.raises(ChannelConfigError):
        load_channels(tmp_path)  # TEST_TG_TOKEN unset in this test


def test_inbound_on_a_send_only_provider_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_DC_HOOK", "https://discord.example/hook")
    doc = {
        **WS,
        "channels": {"eng": {"provider": "discord", "credentials_ref": "dc", "inbound": True}},
        "credentials": {"dc": CREDENTIALS["dc"]},
    }
    (tmp_path / "workspace.yaml").write_text(yaml.safe_dump(doc))
    with pytest.raises(ChannelConfigError, match="inbound is only supported"):
        load_channels(tmp_path)


def test_env_var_in_config_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`chat_id: "${TELEGRAM_CHAT_ID}"` must reach Telegram as the id, not as the literal string.

    Unexpanded, the API answers "chat not found" — an error naming the platform rather than the
    variable nobody set. A destination is not a secret, so `${VAR}` is the ordinary way to write
    it and it has to work.
    """
    monkeypatch.setenv("TEST_TG_TOKEN", "bot-token-123")
    monkeypatch.setenv("MY_CHAT", "-100777")
    doc = {
        **WS,
        "channels": {
            "ops": {
                "provider": "telegram",
                "credentials_ref": "tg",
                "config": {"chat_id": "${MY_CHAT}"},
            }
        },
    }
    (tmp_path / "workspace.yaml").write_text(yaml.safe_dump(doc))
    assert load_channels(tmp_path)["ops"].config["chat_id"] == "-100777"


# ---- the tools ----------------------------------------------------------------------------


@pytest.fixture
def loaded(workspace: Path) -> None:
    srv._set_workspace(workspace)
    srv._offsets.clear()
    srv._poll_owner.clear()


@pytest.mark.asyncio
async def test_send_delivers_through_the_provider(loaded: None) -> None:
    with patch.object(srv, "_send", AsyncMock(return_value=True)) as sent:
        out = await srv.channel_send("ops", "the build is green")
    assert out == {"delivered": True, "channel": "ops"}
    assert sent.await_args is not None
    assert sent.await_args.args[1] == "the build is green"


@pytest.mark.asyncio
async def test_unknown_channel_lists_the_configured_ones(loaded: None) -> None:
    with pytest.raises(ChannelConfigError, match=r"\['eng', 'ops'\]"):
        await srv.channel_send("nope", "hi")


@pytest.mark.asyncio
async def test_ask_returns_the_reply(loaded: None) -> None:
    replies = [[], [{"id": 2, "text": "ship it", "from": "srijith", "at": 0}]]
    with (
        patch.object(srv, "_send", AsyncMock(return_value=True)),
        patch.object(srv, "_fetch_telegram", AsyncMock(side_effect=[[], *replies])),
    ):
        out = await srv.channel_ask("ops", "deploy?", timeout_s=5)
    assert out["answered"] is True
    assert out["text"] == "ship it"


@pytest.mark.asyncio
async def test_ask_timing_out_is_a_normal_return(loaded: None) -> None:
    """Not an exception: an agent that reached nobody should say so and carry on."""
    with (
        patch.object(srv, "_send", AsyncMock(return_value=True)),
        patch.object(srv, "_fetch_telegram", AsyncMock(return_value=[])),
        patch.object(srv, "_POLL_INTERVAL_S", 0.01),
    ):
        out = await srv.channel_ask("ops", "deploy?", timeout_s=1)
    assert out == {"answered": False, "reason": "timeout", "waited_s": 1.0}


@pytest.mark.asyncio
async def test_ask_on_a_send_only_channel_does_not_send(loaded: None) -> None:
    """Asking where nobody can answer strands the human as well as the agent."""
    with patch.object(srv, "_send", AsyncMock(return_value=True)) as sent:
        out = await srv.channel_ask("eng", "deploy?")
    assert out["reason"] == "unsupported"
    sent.assert_not_awaited()


def test_timeout_is_clamped_at_both_ends() -> None:
    """A ceiling so a run cannot hang on a human at lunch; a floor so 0 is not an instant 'no'."""
    assert srv._bound(99999) == srv.MAX_TIMEOUT_S
    assert srv._bound(0) == 1.0
    assert srv._bound(42) == 42.0


@pytest.mark.asyncio
async def test_replies_on_send_only_is_unsupported_not_empty(loaded: None) -> None:
    """An empty list reads as 'nobody answered'. That is a different claim from 'we cannot hear'."""
    out = await srv.channel_replies("eng")
    assert out["supported"] is False
    assert "messages" not in out


def test_second_poller_on_one_token_is_refused_by_name(workspace: Path) -> None:
    """getUpdates is single-consumer: two pollers steal each other's updates, silently."""
    doc = yaml.safe_load((workspace / "workspace.yaml").read_text())
    doc["channels"]["ops2"] = dict(doc["channels"]["ops"])
    (workspace / "workspace.yaml").write_text(yaml.safe_dump(doc))
    srv._set_workspace(workspace)
    srv._poll_owner.clear()

    srv._claim_poller(srv._get("ops"))
    with pytest.raises(ChannelConfigError, match="already polled by channel 'ops'"):
        srv._claim_poller(srv._get("ops2"))


def test_channels_list_reports_who_can_answer(loaded: None) -> None:
    listed = {c["id"]: c for c in srv.channels_list()}
    assert listed["ops"]["can_receive"] is True
    assert listed["eng"]["can_receive"] is False


# ---- inbound filtering --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stranger_in_another_chat_is_not_an_answer(loaded: None) -> None:
    """A bot may sit in several chats. Another chat's message must not answer this question."""
    payload = {
        "result": [
            {"update_id": 7, "message": {"text": "unrelated", "chat": {"id": -100111}}},
            {"update_id": 8, "message": {"text": "yes", "chat": {"id": -100999}}},
        ]
    }

    class _Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return payload

    class _Client:
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, *_: object, **__: object) -> _Resp:
            return _Resp()

    with patch("swarmkit_runtime.channels._server.httpx.AsyncClient", lambda **_: _Client()):
        got = await srv._fetch_telegram(srv._get("ops"), timeout_s=0)
    assert [m["text"] for m in got] == ["yes"]
