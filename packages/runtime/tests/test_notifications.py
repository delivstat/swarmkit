"""Tests for notification plugin system (M6 PR 6)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx as httpx_mod
import pytest
from swarmkit_runtime.notifications import (
    DiscordNotificationProvider,
    NotificationEvent,
    NotificationProvider,
    NotificationRegistry,
    NotificationStore,
    SlackNotificationProvider,
    TelegramNotificationProvider,
    TerminalNotificationProvider,
    WebhookNotificationProvider,
)
from swarmkit_runtime.notifications._providers import build_provider


def _make_event(
    event_type: str = "hitl_requested",
    run_id: str = "run-001",
    topology_id: str = "code-review",
    summary: str = "Human approval needed for deploy",
) -> NotificationEvent:
    return NotificationEvent(
        event_type=event_type,  # type: ignore[arg-type]
        run_id=run_id,
        topology_id=topology_id,
        summary=summary,
        metadata={"agent_id": "resolution-agent"},
    )


class TestNotificationEvent:
    def test_fields(self) -> None:
        event = _make_event()
        assert event.event_type == "hitl_requested"
        assert event.run_id == "run-001"
        assert event.topology_id == "code-review"
        assert event.summary == "Human approval needed for deploy"
        assert event.metadata["agent_id"] == "resolution-agent"


class TestTerminalProvider:
    @pytest.mark.asyncio
    async def test_prints_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        provider = TerminalNotificationProvider()
        result = await provider.notify(_make_event())
        assert result is True
        captured = capsys.readouterr()
        assert "[REVIEW]" in captured.err
        assert "Human approval needed" in captured.err

    @pytest.mark.asyncio
    async def test_error_event(self, capsys: pytest.CaptureFixture[str]) -> None:
        provider = TerminalNotificationProvider()
        await provider.notify(_make_event(event_type="run_ended_error", summary="Agent crashed"))
        captured = capsys.readouterr()
        assert "[ERROR]" in captured.err
        assert "Agent crashed" in captured.err


class TestWebhookProvider:
    @pytest.mark.asyncio
    async def test_sends_json_post(self) -> None:
        provider = WebhookNotificationProvider(url="http://example.com/hook")
        mock_response = AsyncMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            result = await provider.notify(_make_event())

        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["json"]["event_type"] == "hitl_requested"
        assert call_kwargs.kwargs["json"]["summary"] == "Human approval needed for deploy"

    @pytest.mark.asyncio
    async def test_returns_false_on_http_error(self) -> None:
        provider = WebhookNotificationProvider(url="http://example.com/hook")
        mock_response = AsyncMock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient.post", return_value=mock_response):
            result = await provider.notify(_make_event())

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_network_error(self) -> None:
        provider = WebhookNotificationProvider(url="http://unreachable.invalid/hook")
        with patch("httpx.AsyncClient.post", side_effect=httpx_mod.ConnectError("fail")):
            result = await provider.notify(_make_event())
        assert result is False


class TestSlackProvider:
    @pytest.mark.asyncio
    async def test_sends_formatted_message(self) -> None:
        provider = SlackNotificationProvider(webhook_url="http://hooks.slack.com/xxx")
        mock_response = AsyncMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            result = await provider.notify(_make_event())

        assert result is True
        payload = mock_post.call_args.kwargs["json"]
        assert ":raised_hand:" in payload["text"]
        assert "hitl_requested" in payload["text"]
        assert "Human approval needed" in payload["text"]

    @pytest.mark.asyncio
    async def test_includes_channel(self) -> None:
        provider = SlackNotificationProvider(
            webhook_url="http://hooks.slack.com/xxx", channel="#alerts"
        )
        mock_response = AsyncMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            await provider.notify(_make_event())

        payload = mock_post.call_args.kwargs["json"]
        assert payload["channel"] == "#alerts"


class TestNotificationRegistry:
    @pytest.mark.asyncio
    async def test_dispatches_to_all_providers(self) -> None:
        registry = NotificationRegistry()
        p1 = TerminalNotificationProvider()
        p2 = TerminalNotificationProvider()
        registry.register(p1)
        registry.register(p2)

        results = await registry.dispatch(_make_event())
        assert len(results) == 2
        assert all(results)

    @pytest.mark.asyncio
    async def test_event_filter(self) -> None:
        registry = NotificationRegistry()
        hitl_only = TerminalNotificationProvider()
        all_events = TerminalNotificationProvider()

        registry.register(hitl_only, events=["hitl_requested"])
        registry.register(all_events, events=[])

        results = await registry.dispatch(_make_event(event_type="run_ended_error"))
        assert len(results) == 1  # only all_events fires

        results = await registry.dispatch(_make_event(event_type="hitl_requested"))
        assert len(results) == 2  # both fire

    @pytest.mark.asyncio
    async def test_provider_error_doesnt_crash(self) -> None:
        class FailingProvider(NotificationProvider):
            provider_id = "failing"

            async def notify(self, event: NotificationEvent) -> bool:
                raise RuntimeError("boom")

        registry = NotificationRegistry()
        registry.register(FailingProvider())
        registry.register(TerminalNotificationProvider())

        results = await registry.dispatch(_make_event())
        assert results == [False, True]

    def test_provider_count(self) -> None:
        registry = NotificationRegistry()
        assert registry.provider_count == 0
        registry.register(TerminalNotificationProvider())
        assert registry.provider_count == 1


class TestDiscordProvider:
    @pytest.mark.asyncio
    async def test_sends_embed(self) -> None:
        provider = DiscordNotificationProvider(webhook_url="http://discord.com/api/webhooks/xxx")
        mock_response = AsyncMock()
        mock_response.status_code = 204

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            result = await provider.notify(_make_event())

        assert result is True
        payload = mock_post.call_args.kwargs["json"]
        assert payload["username"] == "SwarmKit"
        assert len(payload["embeds"]) == 1
        assert payload["embeds"][0]["title"] == "hitl_requested"
        assert "Human approval needed" in payload["embeds"][0]["description"]
        assert payload["embeds"][0]["color"] == 0xFFA500


class TestTelegramProvider:
    @pytest.mark.asyncio
    async def test_sends_message(self) -> None:
        provider = TelegramNotificationProvider(bot_token="123:ABC", chat_id="-100123")
        mock_response = AsyncMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            result = await provider.notify(_make_event())

        assert result is True
        call_url = mock_post.call_args.args[0]
        assert "bot123:ABC/sendMessage" in call_url
        payload = mock_post.call_args.kwargs["json"]
        assert payload["chat_id"] == "-100123"
        assert payload["parse_mode"] == "Markdown"
        assert "hitl_requested" in payload["text"]
        assert "Human approval needed" in payload["text"]


class TestBuildProvider:
    def test_terminal(self) -> None:
        p = build_provider("terminal", {})
        assert isinstance(p, TerminalNotificationProvider)

    def test_webhook(self) -> None:
        p = build_provider("webhook", {"url": "http://example.com"})
        assert isinstance(p, WebhookNotificationProvider)

    def test_slack(self) -> None:
        p = build_provider("slack", {"webhook_url": "http://hooks.slack.com/xxx"})
        assert isinstance(p, SlackNotificationProvider)

    def test_discord(self) -> None:
        p = build_provider("discord", {"webhook_url": "http://discord.com/api/webhooks/xxx"})
        assert isinstance(p, DiscordNotificationProvider)

    def test_telegram(self) -> None:
        p = build_provider("telegram", {"bot_token": "123:ABC", "chat_id": "-100123"})
        assert isinstance(p, TelegramNotificationProvider)

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown notification provider"):
            build_provider("nonexistent", {})


class TestNotificationStore:
    def test_create_and_query(self, tmp_path: Path) -> None:
        store = NotificationStore(db_path=tmp_path / "notifications.sqlite")
        notif_id = store.create(_make_event())

        assert notif_id is not None
        records = store.query()
        assert len(records) == 1
        assert records[0].id == notif_id
        assert records[0].status == "pending"
        assert records[0].event_type == "hitl_requested"
        assert records[0].summary == "Human approval needed for deploy"
        store.close()

    def test_mark_delivered(self, tmp_path: Path) -> None:
        store = NotificationStore(db_path=tmp_path / "notifications.sqlite")
        notif_id = store.create(_make_event())
        store.mark_delivered(notif_id, "slack")

        records = store.query()
        assert records[0].status == "delivered"
        assert records[0].provider == "slack"
        assert records[0].delivered_at is not None
        store.close()

    def test_mark_failed(self, tmp_path: Path) -> None:
        store = NotificationStore(db_path=tmp_path / "notifications.sqlite")
        notif_id = store.create(_make_event())
        store.mark_failed(notif_id, "webhook", "connection refused")

        records = store.query()
        assert records[0].status == "failed"
        assert records[0].provider == "webhook"
        assert records[0].error == "connection refused"
        store.close()

    def test_query_by_status(self, tmp_path: Path) -> None:
        store = NotificationStore(db_path=tmp_path / "notifications.sqlite")
        id1 = store.create(_make_event())
        store.create(_make_event(summary="second"))
        store.mark_delivered(id1, "slack")

        pending = store.query(status="pending")
        assert len(pending) == 1
        assert pending[0].summary == "second"

        delivered = store.query(status="delivered")
        assert len(delivered) == 1
        assert delivered[0].id == id1
        store.close()

    def test_query_by_run_id(self, tmp_path: Path) -> None:
        store = NotificationStore(db_path=tmp_path / "notifications.sqlite")
        store.create(_make_event(run_id="run-1"))
        store.create(_make_event(run_id="run-2"))

        results = store.query(run_id="run-1")
        assert len(results) == 1
        assert results[0].run_id == "run-1"
        store.close()

    def test_count(self, tmp_path: Path) -> None:
        store = NotificationStore(db_path=tmp_path / "notifications.sqlite")
        store.create(_make_event())
        id2 = store.create(_make_event())
        store.mark_delivered(id2, "terminal")

        assert store.count() == 2
        assert store.count(status="pending") == 1
        assert store.count(status="delivered") == 1
        store.close()

    def test_persists_across_instances(self, tmp_path: Path) -> None:
        db = tmp_path / "notifications.sqlite"
        store1 = NotificationStore(db_path=db)
        store1.create(_make_event())
        store1.close()

        store2 = NotificationStore(db_path=db)
        assert store2.count() == 1
        store2.close()

    def test_to_dict(self, tmp_path: Path) -> None:
        store = NotificationStore(db_path=tmp_path / "notifications.sqlite")
        store.create(_make_event())
        record = store.query()[0]
        d = record.to_dict()
        assert d["event_type"] == "hitl_requested"
        assert d["status"] == "pending"
        assert "id" in d
        assert "created_at" in d
        store.close()


class TestRegistryWithStore:
    @pytest.mark.asyncio
    async def test_dispatch_persists_to_store(self, tmp_path: Path) -> None:
        store = NotificationStore(db_path=tmp_path / "notifications.sqlite")
        registry = NotificationRegistry(store=store)
        registry.register(TerminalNotificationProvider())

        await registry.dispatch(_make_event())

        records = store.query()
        assert len(records) == 1
        assert records[0].status == "delivered"
        assert records[0].provider == "terminal"
        store.close()

    @pytest.mark.asyncio
    async def test_dispatch_records_failure(self, tmp_path: Path) -> None:
        class FailingProvider(NotificationProvider):
            provider_id = "failing"

            async def notify(self, event: NotificationEvent) -> bool:
                raise RuntimeError("boom")

        store = NotificationStore(db_path=tmp_path / "notifications.sqlite")
        registry = NotificationRegistry(store=store)
        registry.register(FailingProvider())

        await registry.dispatch(_make_event())

        records = store.query()
        assert len(records) == 1
        assert records[0].status == "failed"
        assert records[0].error == "boom"
        store.close()

    @pytest.mark.asyncio
    async def test_dispatch_without_store(self) -> None:
        registry = NotificationRegistry(store=None)
        registry.register(TerminalNotificationProvider())
        results = await registry.dispatch(_make_event())
        assert results == [True]
