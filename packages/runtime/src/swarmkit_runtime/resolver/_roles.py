"""Role-registry resolution — merges discovered RoleRegistry artifacts into one registry.

A workspace's roles (design/details/multi-party-approval.md) map role ids to member
identities and the governance scopes they confer. Discovered under ``roles/``; merged
into a single :class:`RoleRegistry` on :class:`ResolvedWorkspace` and consumed by a
funnel's ``approve`` layer (multi-party approval) to check quorum across identities.
"""

from __future__ import annotations

from collections.abc import Iterable

from swarmkit_runtime.errors import ResolutionError
from swarmkit_runtime.governance._approval import Role, RoleRegistry
from swarmkit_runtime.workspace import DiscoveredArtifact


def build_role_registry(
    artifacts: Iterable[DiscoveredArtifact],
) -> tuple[RoleRegistry, list[ResolutionError]]:
    """Merge every discovered ``role-registry`` artifact into one registry.

    Each artifact has already passed JSON-Schema validation. A role id declared in
    two registries is a conflict (registries are additive but ids must be unique) and
    surfaces as a structured error.
    """
    errors: list[ResolutionError] = []
    roles: dict[str, Role] = {}

    for artifact in artifacts:
        if artifact.kind != "role-registry":
            continue
        raw = dict(artifact.raw)
        for entry in raw.get("roles", []) or []:
            role_id = str(entry.get("id", ""))
            if role_id in roles:
                errors.append(
                    ResolutionError(
                        code="role.duplicate-id",
                        message=f"Role id {role_id!r} is declared in more than one registry.",
                        artifact_path=artifact.path,
                        yaml_pointer="/roles",
                        suggestion="Give every role a unique id across role registries.",
                    )
                )
                continue
            roles[role_id] = Role(
                id=role_id,
                members=frozenset(entry.get("members", []) or []),
                scopes=frozenset(entry.get("scopes", []) or []),
            )

    return RoleRegistry(roles=roles), errors


__all__ = ["build_role_registry"]
