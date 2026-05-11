"""Audit provider abstraction and built-in implementations.

The AuditProvider interface defines pluggable audit event storage.
GovernanceProvider.record_event() delegates to the configured provider.

Built-in implementations:
  - mock: in-memory (tests, development)
  - sqlite: local SQLite (default for production single-node)

See design/details/human-interaction-model.md for the event schema,
design/details/opentelemetry-observability.md for OTel integration.
"""

from swarmkit_runtime.audit._mock import MockAuditProvider
from swarmkit_runtime.audit._provider import AuditProvider, AuditProviderRegistry, get_registry
from swarmkit_runtime.audit._redact import apply_audit_policy, resolve_audit_config
from swarmkit_runtime.audit._sqlite import SQLiteAuditProvider

# Register built-in providers
_reg = get_registry()
_reg.register("mock", MockAuditProvider)
_reg.register("sqlite", SQLiteAuditProvider)

__all__ = [
    "AuditProvider",
    "AuditProviderRegistry",
    "MockAuditProvider",
    "SQLiteAuditProvider",
    "apply_audit_policy",
    "get_registry",
    "resolve_audit_config",
]
