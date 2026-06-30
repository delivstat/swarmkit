"""Schema-compatibility gate for governed deploy (design §15 "schema-version compatibility").

The registry refuses to deploy an artifact an instance can't validate. An artifact records the
``swarmkit-schema`` version it validated against; an instance reports its own schema version. A
deploy is compatible when they share a major version and the instance's schema is at least as new as
the artifact's (same major, instance minor ≥ artifact minor) — a different major needs a migration.

When either side's schema version is unknown (empty / unparseable) the gate can't prove
incompatibility, so it allows the deploy rather than blocking on missing data.
"""

from __future__ import annotations


def _parse(version: str) -> tuple[int, int] | None:
    """Parse 'MAJOR.MINOR[.PATCH]' → (major, minor); None if not parseable."""
    parts = version.strip().split(".")
    if len(parts) < 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def schema_compatible(artifact_schema: str, instance_schema: str) -> bool:
    """True if *instance_schema* can validate an artifact built for *artifact_schema*."""
    a = _parse(artifact_schema)
    i = _parse(instance_schema)
    if a is None or i is None:
        return True  # unknown → not gated
    return a[0] == i[0] and i[1] >= a[1]


def incompatibility(artifact_schema: str, instance_schema: str) -> str | None:
    """A reason string if the deploy is schema-incompatible, else None."""
    if schema_compatible(artifact_schema, instance_schema):
        return None
    return (
        f"artifact needs swarmkit-schema {artifact_schema}, but the instance is on "
        f"{instance_schema}"
    )
