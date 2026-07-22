"""Contract resolution — builds the workspace integration-contract registry.

A Contract (design/details/contract-registry.md) is the agreed interface between apps, discovered
under ``contracts/`` and referenced by a StageGraph stage's ``locks``. Resolving them into a
registry makes lock ids a checked vocabulary — the stage-graph resolver rejects a lock that names
no contract.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import ValidationError as PydanticValidationError
from swarmkit_schema.models import SwarmKitContract

from swarmkit_runtime.errors import ResolutionError
from swarmkit_runtime.workspace import DiscoveredArtifact

from ._resolved import ResolvedContract


def build_contract_registry(
    artifacts: Iterable[DiscoveredArtifact],
) -> tuple[dict[str, ResolvedContract], list[ResolutionError]]:
    """Build the ``id -> ResolvedContract`` registry from discovered contract artifacts."""
    errors: list[ResolutionError] = []
    contracts: dict[str, ResolvedContract] = {}

    for artifact in artifacts:
        if artifact.kind != "contract":
            continue
        raw = dict(artifact.raw)
        try:
            model = SwarmKitContract.model_validate(raw)
        except PydanticValidationError as exc:
            errors.append(
                ResolutionError(
                    code="contract.model-construction",
                    message=(
                        f"contract at {artifact.path} could not be constructed "
                        "as a pydantic SwarmKitContract model."
                    ),
                    artifact_path=artifact.path,
                    suggestion=f"pydantic raised: {exc.errors()[0]['msg']}",
                )
            )
            continue

        contract_id = str((raw.get("metadata") or {}).get("id", ""))
        if contract_id in contracts:
            errors.append(
                ResolutionError(
                    code="contract.duplicate-id",
                    message=f"Contract id {contract_id!r} is declared twice.",
                    artifact_path=artifact.path,
                    yaml_pointer="/metadata/id",
                    suggestion="Rename one of the contracts so every id is unique.",
                )
            )
            continue

        contracts[contract_id] = ResolvedContract(
            id=contract_id,
            raw=model,
            source_path=artifact.path,
            parties=tuple(str(p) for p in raw.get("parties", []) or []),
        )

    return contracts, errors


__all__ = ["build_contract_registry"]
