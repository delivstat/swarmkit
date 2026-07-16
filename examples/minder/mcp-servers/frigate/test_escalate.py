"""Standalone tests for severity + VLM escalation (design/scenario-studio-severity-escalation.md).

An escalate rule gates its alert on a VLM yes/no confirmation of the snapshot: only a
matching answer fires the alert + actions; a trusted 'no' suppresses it; when the VLM
can't be reached a *critical* rule fires 'unverified' (never miss), a non-critical drops.
cooldown_s rate-limits.

Run in-container:  docker compose exec -T minder python /app/mcp-servers/frigate/test_escalate.py
"""

import os
import tempfile

import server as f


class _SyncThread:
    """Run the escalate worker inline so the test is deterministic (no real thread)."""

    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        if self._target:
            self._target()


def _snapshot() -> str:
    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.write(fd, b"\xff\xd8\xff\xe0jpeg-bytes")
    os.close(fd)
    return path


def _harness(*, answer, provider="cloud", snapshot=None):
    """Wire the escalate path: capture alerts, stub the snapshot grab + VLM verdict,
    run the worker synchronously, use an in-memory cooldown store."""
    f.threading.Thread = _SyncThread  # type: ignore[assignment,misc]
    f.RECORD_ENABLED = False  # don't chase a real Frigate clip after a fire
    f.get_event_clip = lambda event_id: ""  # type: ignore
    alerts: list = []
    state: dict = {}
    f.write_alert = lambda msg, cam="", snap="", vid="": alerts.append((msg, cam))  # type: ignore
    f.execute_device_action = lambda d, a: f"{a} {d}"  # type: ignore
    f._escalate_snapshot = lambda ev: "" if snapshot is None else snapshot  # type: ignore
    f._hires_snapshot = lambda cam: ""  # type: ignore  # cloud falls back to the stubbed detect snapshot
    f._vlm_confirm = lambda img, q, tier, model="": (answer, provider)  # type: ignore
    f.load_monitor_state = lambda: state  # type: ignore
    f.save_monitor_state = state.update  # type: ignore
    return alerts, state


_EV = {"camera": "Gate", "label": "person", "source": "frigate", "event_id": "abc123"}


def _rule(**over):
    r = {
        "condition": "person",
        "severity": "critical",
        "escalate": {"tier": "cloud", "prompt": "weapon?", "require": "yes", "cooldown_s": 30},
        "actions": [{"type": "alert"}],
    }
    r.update(over)
    return r


def test_is_yes():
    assert f._is_yes("yes, a knife") is True
    assert f._is_yes("Yes.") is True
    assert f._is_yes("No, just a neighbour") is False
    assert f._is_yes("maybe") is False
    print("ok  _is_yes only matches a leading yes")


def test_escalate_tier():
    assert f._escalate_tier({"severity": "critical", "escalate": {}}) == "cloud"
    assert f._escalate_tier({"severity": "warning", "escalate": {}}) == "local"
    assert f._escalate_tier({"escalate": {"tier": "local"}}) == "local"  # explicit wins
    print("ok  _escalate_tier: auto→severity, explicit tier wins")


def test_confirmed_yes_fires():
    alerts, _ = _harness(answer="Yes, holding a knife.", snapshot=_snapshot())
    f._deliver_escalated(_rule(), _EV, 1000.0)
    assert len(alerts) == 1, alerts
    assert "CRITICAL" in alerts[0][0] and "knife" in alerts[0][0]
    print("ok  confirmed yes → critical alert carrying the VLM reason")


def test_trusted_no_suppresses():
    alerts, _ = _harness(answer="No, just a person walking.", snapshot=_snapshot())
    f._deliver_escalated(_rule(), _EV, 1000.0)
    assert alerts == [], alerts
    print("ok  a trusted 'no' fires NO alert (near-miss)")


def test_critical_unverifiable_fires_unverified():
    alerts, _ = _harness(answer="", provider="", snapshot="")  # no snapshot → can't verify
    f._deliver_escalated(_rule(), _EV, 1000.0)
    assert len(alerts) == 1 and "unverified" in alerts[0][0], alerts
    print("ok  critical + unverifiable → alert flagged (unverified), never missed")


def test_noncritical_unverifiable_drops():
    alerts, _ = _harness(answer="", provider="", snapshot="")
    f._deliver_escalated(_rule(severity="warning"), _EV, 1000.0)
    assert alerts == [], alerts
    print("ok  non-critical + unverifiable → dropped")


def test_notify_action():
    alerts, _ = _harness(answer="Yes, a weapon.", snapshot=_snapshot())
    rule = _rule(actions=[{"type": "alert"}, {"type": "notify", "contact": "police"}])
    f._deliver_escalated(rule, _EV, 1000.0)
    joined = " | ".join(a[0] for a in alerts)
    assert "notifying police" in joined, alerts
    print("ok  notify action → an escalation alert naming the contact")


def test_cooldown_suppresses_refire():
    alerts, _ = _harness(answer="Yes.", snapshot=_snapshot())
    f._deliver_escalated(_rule(), _EV, 1000.0)
    f._deliver_escalated(_rule(), _EV, 1005.0)  # within cooldown_s=30
    assert len(alerts) == 1, alerts
    print("ok  cooldown_s suppresses a re-fire within the window")


def test_cloud_tier_grabs_hires_snapshot():
    # A critical (cloud) rule must request a full-res main-stream frame, not the
    # 352x288 detect snapshot on which a gesture/detail is unresolvable.
    alerts, _ = _harness(answer="Yes, waving.")
    got = {}

    def _hires(cam):
        got["cam"] = cam
        return _snapshot()

    f._hires_snapshot = _hires  # type: ignore
    f._deliver_escalated(_rule(), _EV, 1000.0)  # severity critical → cloud tier
    assert got.get("cam") == "Gate", "cloud tier should request a hi-res snapshot"
    assert len(alerts) == 1
    print("ok  cloud tier grabs a hi-res main-stream snapshot")


def test_local_tier_also_grabs_hires():
    # Both tiers get the full-res frame — a small VLM resolves a gesture far better
    # at full res than at 352x288, and escalate runs async so latency is fine.
    alerts, _ = _harness(answer="Yes.")
    got = {}

    def _hires(cam):
        got["cam"] = cam
        return _snapshot()

    f._hires_snapshot = _hires  # type: ignore
    # no explicit tier → auto → warning → local
    rule = _rule(severity="warning", escalate={"prompt": "waving?", "require": "yes"})
    f._deliver_escalated(rule, _EV, 1000.0)
    assert got.get("cam") == "Gate", "local tier should also grab a hi-res frame"
    assert len(alerts) == 1
    print("ok  local tier also grabs the hi-res frame")


def test_match_prefers_escalate_over_broad_plain():
    # The user's real case: a broad "all cameras / person…" rule listed BEFORE a
    # specific Office escalate rule. The escalate rule must still win.
    fired = {}
    f._fire = lambda rule, ev, now, live: fired.update(rule=rule) or "live"  # type: ignore
    plain = {"cameras": ["all"], "condition": "person standing at the gate or entrance"}
    esc = {
        "cameras": ["Office"],
        "condition": "person",
        "severity": "critical",
        "escalate": {"prompt": "weapon?"},
    }
    active = [plain, esc]  # plain first, as saved
    ev = {"camera": "Office", "label": "person"}
    f._match_and_fire_event(ev, active, {}, 1000.0, True)
    assert fired.get("rule") is esc, "escalate rule should win over the broad plain rule"
    print("ok  _match_and_fire_event prefers the escalate rule over a broad plain rule")


def test_match_plain_still_fires_without_escalate():
    fired = {}
    f._fire = lambda rule, ev, now, live: fired.update(rule=rule) or "live"  # type: ignore
    plain = {"cameras": ["all"], "condition": "person"}
    ev = {"camera": "Office", "label": "person"}
    f._match_and_fire_event(ev, [plain], {}, 1000.0, True)
    assert fired.get("rule") is plain
    print("ok  a plain rule still fires when no escalate rule matches")


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print("\nall escalate tests passed")
