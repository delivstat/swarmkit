"""Standalone test for the Discord QR invite helpers.

Pins: token verification (the analog of Telegram getMe) parses users/@me and
raises on a bad token; the invite URL carries the bot id as client_id plus the
exact permissions bitfield + scope=bot — that URL is what the onboarding QR encodes.

Run in-container:  docker compose exec -T minder python /app/webapp/test_discord_invite.py
"""

import io
import json
import sys

sys.path.insert(0, "/app/webapp")

import app


class _FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen(payload: dict):
    captured = {}

    def fake(req, timeout=0):  # noqa: ANN001
        captured["auth"] = req.get_header("Authorization")
        captured["url"] = req.full_url
        return _FakeResp(json.dumps(payload).encode())

    app.urllib.request.urlopen = fake  # type: ignore[assignment]
    return captured


def test_verify_discord_token_ok():
    cap = _patch_urlopen({"id": "1056789012345678", "username": "minder_bot"})
    bot = app._verify_discord_token("MTA1Njc4.fake.token")
    assert bot["id"] == "1056789012345678"
    assert bot["username"] == "minder_bot"
    assert cap["auth"] == "Bot MTA1Njc4.fake.token", cap
    assert cap["url"].endswith("/users/@me"), cap
    print("ok  _verify_discord_token parses users/@me + sends Bot auth header")


def test_verify_discord_token_bad():
    _patch_urlopen({"message": "401: Unauthorized"})  # no id -> invalid
    try:
        app._verify_discord_token("bad")
    except ValueError:
        print("ok  invalid token (no id) raises ValueError")
        return
    raise AssertionError("expected ValueError on a token with no id")


def test_invite_url_shape():
    url = app._discord_invite_url("1056789012345678")
    assert url.startswith("https://discord.com/oauth2/authorize?"), url
    assert "client_id=1056789012345678" in url, url
    assert "scope=bot" in url, url
    assert f"permissions={app.DISCORD_BOT_PERMS}" in url, url
    # perms = View Channel + Send Messages + Embed Links + Attach Files + Read History
    assert app.DISCORD_BOT_PERMS == 1024 + 2048 + 16384 + 32768 + 65536
    print("ok  _discord_invite_url carries client_id + scope=bot + exact perms")


if __name__ == "__main__":
    test_verify_discord_token_ok()
    test_verify_discord_token_bad()
    test_invite_url_shape()
    print("\nALL DISCORD-INVITE TESTS PASSED")
