"""Standalone test for the active health monitor (report-only).

Pins: probe -> component states, the down-hysteresis (N consecutive fails before
'down'), and that state TRANSITIONS publish a report alert (and nothing else —
report-only never restarts).

Run in-container:  docker compose exec -T minder python /app/webapp/test_health_monitor.py
"""

import sys

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/mcp-servers")

import _alert_sink
import health_monitor as hm


def test_probe_states_up():
    hm._tcp = lambda h, p, timeout=3: True
    hm._http_ok = lambda u, timeout=4: True
    hm._proc_alive = lambda n: True
    hm._channel_token = lambda c: "tok" if c == "telegram" else ""
    comps = {c["id"]: c for c in hm._probe()}
    assert comps["swarmkit"]["state"] == "ok"
    assert comps["ollama"]["state"] == "ok"
    assert comps["telegram"]["state"] == "ok"  # token + process
    assert comps["discord"]["state"] == "off"  # no token -> not configured
    print("ok  probe maps reachable + configured -> ok / off")


def test_probe_states_down():
    hm._tcp = lambda h, p, timeout=3: False
    hm._http_ok = lambda u, timeout=4: False
    hm._proc_alive = lambda n: True
    hm._channel_token = lambda c: ""
    comps = {c["id"]: c for c in hm._probe()}
    assert comps["mosquitto"]["state"] == "down"
    assert comps["ollama"]["state"] == "down"
    print("ok  probe maps unreachable -> down")


def test_hysteresis():
    hm._fails = {}
    one = [{"id": "ollama", "name": "Ollama", "state": "down", "detail": ""}]
    hm._apply_hysteresis(one, {"ollama": "ok"})
    assert one[0]["state"] == "ok", "1st failure must not flip to down (blip)"
    two = [{"id": "ollama", "name": "Ollama", "state": "down", "detail": ""}]
    hm._apply_hysteresis(two, {"ollama": "ok"})
    assert two[0]["state"] == "down", "2nd consecutive failure -> down"
    print("ok  hysteresis holds a single blip, flips on sustained failure")


def test_transition_alerts_report_only():
    sent = []
    _alert_sink.write_alert = lambda msg, cam="", *a, **k: sent.append(msg)
    hm._alert_transitions(
        [{"id": "ollama", "name": "Ollama", "state": "down", "detail": "unreachable"}],
        {"ollama": "ok"},
    )
    assert any("DOWN" in m for m in sent), sent
    sent.clear()
    hm._alert_transitions(
        [{"id": "ollama", "name": "Ollama", "state": "ok", "detail": ""}], {"ollama": "down"}
    )
    assert any("recovered" in m for m in sent), sent
    sent.clear()
    hm._alert_transitions([{"id": "ollama", "name": "Ollama", "state": "ok"}], {"ollama": "ok"})
    assert sent == [], "no transition -> no alert"
    print("ok  transitions report (down + recovered); steady state is silent")


if __name__ == "__main__":
    test_probe_states_up()
    test_probe_states_down()
    test_hysteresis()
    test_transition_alerts_report_only()
    print("\nALL HEALTH-MONITOR TESTS PASSED")
