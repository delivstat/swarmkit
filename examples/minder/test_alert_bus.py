"""Live end-to-end test for the MQTT alert bus.

Publishes via write_alert and receives via the adapter subscriber against the
REAL mosquitto broker — proving the fan-out path (producer -> topic -> durable
subscriber) without needing any Telegram/Discord tokens. Uses a test topic +
temp data dir so it never touches the real channels or events.json.

Run in-container:  docker compose exec -T minder python /app/test_alert_bus.py
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")  # alert_bus
sys.path.insert(0, "/app/mcp-servers")  # _alert_sink

import _alert_sink
import alert_bus

TEST_TOPIC = "minder/test-alerts"


async def _run() -> None:
    # isolate: test topic (not the real bot's minder/alerts) + temp events.json
    _alert_sink.ALERTS_TOPIC = TEST_TOPIC
    alert_bus.ALERTS_TOPIC = TEST_TOPIC
    _alert_sink.DATA_DIR = Path(tempfile.mkdtemp())

    received: list[dict] = []

    async def on_alert(alert: dict) -> None:
        received.append(alert)

    loop = asyncio.get_running_loop()
    client = alert_bus.start_alert_subscriber("minder-test", loop, on_alert)
    await asyncio.sleep(1.5)  # connect + subscribe

    _alert_sink.write_alert("bus test alert", camera="TestCam")  # publishes to TEST_TOPIC
    for _ in range(20):  # up to ~4s for delivery
        if received:
            break
        await asyncio.sleep(0.2)
    client.loop_stop()

    assert received, "no alert received over MQTT"
    a = received[0]
    assert a["message"] == "bus test alert" and a["camera"] == "TestCam", a
    print("ok  write_alert -> mosquitto -> durable subscriber received the alert")


if __name__ == "__main__":
    asyncio.run(_run())
    print("\nALL ALERT-BUS TESTS PASSED")
