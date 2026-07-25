"""OMS order module — a tiny stub the harness-build slice (slice 7) targets.

The ``developer`` harness produces a *candidate diff* against this file (adding an
order-status lookup); the code-review gate decides advance / route-back. This is a
fixture, not runtime code — it is never imported by the runtime or the tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Order:
    """A single customer order and its lifecycle state."""

    id: str
    items: list[str] = field(default_factory=list)
    state: str = "created"


_ORDERS: dict[str, Order] = {}


def submit_order(order_id: str, items: list[str]) -> Order:
    """Create an order in the ``created`` state and record it."""
    order = Order(id=order_id, items=items)
    _ORDERS[order_id] = order
    return order
