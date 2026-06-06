"""Tests for trigger scheduler and webhook signature validation.

Covers:
- HMAC-SHA256 webhook signature validation
- TriggerScheduler start/stop lifecycle
- GET /triggers endpoint
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from swarmkit_runtime.triggers._webhook import validate_webhook_signature

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"


# ---------------------------------------------------------------------------
# Webhook signature tests
# ---------------------------------------------------------------------------


def test_webhook_signature_valid() -> None:
    body = b'{"event": "push"}'
    secret = "super-secret"
    expected_hex = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    signature = f"sha256={expected_hex}"
    assert validate_webhook_signature(body, signature, secret) is True


def test_webhook_signature_invalid() -> None:
    body = b'{"event": "push"}'
    secret = "super-secret"
    signature = "sha256=badhex1234"
    assert validate_webhook_signature(body, signature, secret) is False


def test_webhook_signature_wrong_prefix() -> None:
    body = b"data"
    secret = "s3cr3t"
    good_hex = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # Correct hex but missing prefix
    assert validate_webhook_signature(body, good_hex, secret) is False


def test_webhook_signature_unsupported_algorithm() -> None:
    body = b"data"
    signature = "sha512=abc"
    assert validate_webhook_signature(body, signature, "s", algorithm="sha512") is False


def test_webhook_signature_tampered_body() -> None:
    original = b"original body"
    secret = "tok"
    hex_ = hmac.new(secret.encode(), original, hashlib.sha256).hexdigest()
    signature = f"sha256={hex_}"
    # Different body — should fail
    assert validate_webhook_signature(b"tampered body", signature, secret) is False


# ---------------------------------------------------------------------------
# Scheduler start/stop tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_start_stop() -> None:
    from swarmkit_runtime.triggers._scheduler import TriggerScheduler  # noqa: PLC0415

    fire_fn = AsyncMock()
    scheduler = TriggerScheduler(triggers=[], fire_fn=fire_fn, poll_interval=1)

    await scheduler.start()
    assert scheduler._task is not None
    assert not scheduler._task.done()

    await scheduler.stop()
    assert scheduler._task is None


@pytest.mark.asyncio
async def test_scheduler_no_croniter_skips_cron_triggers() -> None:
    """Scheduler must not crash when croniter is absent; cron triggers are skipped."""
    from unittest.mock import patch  # noqa: PLC0415

    from swarmkit_runtime.triggers import _scheduler as sched_mod  # noqa: PLC0415

    fire_fn = AsyncMock()
    triggers = [
        {
            "id": "daily",
            "type": "cron",
            "enabled": True,
            "targets": ["hello"],
            "config": {"expression": "0 9 * * *"},
        }
    ]

    with patch.object(sched_mod, "_croniter_available", False):
        scheduler = sched_mod.TriggerScheduler(triggers=triggers, fire_fn=fire_fn, poll_interval=1)
        await scheduler.start()
        await asyncio.sleep(0.1)
        await scheduler.stop()

    fire_fn.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_disabled_trigger_not_fired() -> None:
    """Triggers with enabled=False must not fire."""
    from unittest.mock import patch  # noqa: PLC0415

    from swarmkit_runtime.triggers import _scheduler as sched_mod  # noqa: PLC0415

    fire_fn = AsyncMock()
    triggers = [
        {
            "id": "off-trigger",
            "type": "cron",
            "enabled": False,
            "targets": ["hello"],
            "config": {"expression": "* * * * *"},
        }
    ]

    with patch.object(sched_mod, "_croniter_available", True):
        scheduler = sched_mod.TriggerScheduler(triggers=triggers, fire_fn=fire_fn, poll_interval=1)
        await scheduler.start()
        await asyncio.sleep(0.1)
        await scheduler.stop()

    fire_fn.assert_not_called()


# ---------------------------------------------------------------------------
# GET /triggers endpoint test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


@pytest.fixture()
def hello_client() -> TestClient:  # type: ignore[misc]
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    app = create_app(EXAMPLE_WS)
    with TestClient(app) as client:
        yield client


def test_triggers_endpoint(hello_client: TestClient) -> None:
    resp = hello_client.get("/triggers")
    assert resp.status_code == 200
    data = resp.json()
    # hello-swarm workspace may have zero triggers — that's fine.
    assert isinstance(data, list)
