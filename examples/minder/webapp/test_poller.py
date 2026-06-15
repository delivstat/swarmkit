"""Standalone test for the deterministic Frigate poll backstop.

poll_events is deterministic — the backstop just calls it on a timer with no LLM.
This pins one cycle: it calls poll_events, parses the result, swallows errors
(the loop must never die), and stays quiet unless something actually fired.

Run in-container:  docker compose exec -T minder python /app/webapp/test_poller.py
"""

import json
from types import SimpleNamespace

import frigate_poller as p


def _stub_frigate(result=None, raises=False):
    """Make _frigate_mod() return a fake module whose poll_events yields `result`
    (or raises). One call recorded so we can assert exactly-once per cycle."""
    calls = {"n": 0}

    def poll_events():
        calls["n"] += 1
        if raises:
            raise RuntimeError("frigate down")
        return json.dumps(result)

    p._frigate = SimpleNamespace(poll_events=poll_events)  # type: ignore
    return calls


def test_poll_once_calls_tool_once():
    calls = _stub_frigate({"status": "ok", "alerts": 0, "events_seen": 3})
    res = p._poll_once()
    assert calls["n"] == 1  # exactly one deterministic call — no agent loop
    assert res["events_seen"] == 3
    print("ok  _poll_once calls poll_events exactly once")


def test_poll_once_returns_activity():
    _stub_frigate({"status": "ok", "alerts": 2, "time_rules_fired": 1, "events_seen": 5})
    res = p._poll_once()
    assert res["alerts"] == 2 and res["time_rules_fired"] == 1
    print("ok  _poll_once surfaces fired alerts")


def test_poll_once_swallows_errors():
    # transport/parse failure must NOT propagate — the backstop keeps running
    _stub_frigate(raises=True)
    assert p._poll_once() is None
    print("ok  _poll_once never raises (loop survives a bad cycle)")


def test_poll_once_handles_tool_error_status():
    _stub_frigate({"status": "error", "message": "frigate http 500"})
    res = p._poll_once()
    assert res["status"] == "error"  # logged, not raised
    print("ok  _poll_once handles error status gracefully")


if __name__ == "__main__":
    test_poll_once_calls_tool_once()
    test_poll_once_returns_activity()
    test_poll_once_swallows_errors()
    test_poll_once_handles_tool_error_status()
    print("\nALL POLLER TESTS PASSED")
