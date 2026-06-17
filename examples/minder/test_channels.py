"""Standalone test for channel token resolution.

Adapters resolve their token via alert_bus.channel_token (config-first) so the
dashboard can enable a channel with no container restart. Pins: config token,
disabled channel, legacy top-level telegram_token, and env fallback.

Run in-container:  docker compose exec -T minder python /app/test_channels.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")
import alert_bus


def _cfg(d: dict) -> None:
    tmp = Path(tempfile.mkdtemp())
    (tmp / "minder-config.json").write_text(json.dumps(d))
    alert_bus.CONFIG_FILE = tmp / "minder-config.json"
    os.environ.pop("MINDER_DISCORD_TOKEN", None)


def test_config_token():
    _cfg({"channels": {"discord": {"enabled": True, "token": "D123"}}})
    assert alert_bus.channel_token("discord") == "D123"
    print("ok  config channels.<id>.token resolves")


def test_disabled_channel_ignored():
    _cfg({"channels": {"discord": {"enabled": False, "token": "D123"}}})
    assert alert_bus.channel_token("discord") == ""  # disabled -> no token
    print("ok  disabled channel yields no token")


def test_legacy_telegram_top_level():
    _cfg({"telegram_token": "TG999"})
    assert alert_bus.channel_token("telegram") == "TG999"
    print("ok  legacy top-level telegram_token resolves")


def test_env_fallback():
    _cfg({})
    os.environ["MINDER_DISCORD_TOKEN"] = "ENVTOK"
    try:
        assert alert_bus.channel_token("discord") == "ENVTOK"
    finally:
        os.environ.pop("MINDER_DISCORD_TOKEN", None)
    print("ok  env var fallback resolves")


if __name__ == "__main__":
    test_config_token()
    test_disabled_channel_ignored()
    test_legacy_telegram_top_level()
    test_env_fallback()
    print("\nALL CHANNELS TESTS PASSED")
