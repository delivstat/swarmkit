"""Audit and skill-gap logging (design §5.4, §14.5, §16.4).

Audit events flow into AGT's Agent SRE telemetry pipeline via the
`GovernanceProvider.record_event` interface. This module adds SwarmKit-specific
event types on top:

  - skill gap events (design §12.1)
  - review queue state changes
  - gap surfacing decisions
  - authoring swarm conversations

All writes are append-only. No code path in this module exposes update/delete.
"""
