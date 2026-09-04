"""Channel skills — a swarm reaches a human on the channel they already use.

`design/details/channel-skills.md`. The transports are the notification providers; this package is
what makes them addressable by an agent, and what carries a reply back.
"""

from swarmkit_runtime.channels._config import (
    INBOUND_CAPABLE,
    Channel,
    ChannelConfigError,
    load_channels,
)

__all__ = ["INBOUND_CAPABLE", "Channel", "ChannelConfigError", "load_channels"]
