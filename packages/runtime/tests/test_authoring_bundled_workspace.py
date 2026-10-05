"""Foundation tests for the bundled authoring workspace (#1045).

PR 1 only wires the scaffold + resolver. These tests prove:

* the bundled workspace exists on disk at the resolver-reported path,
* every advertised mode has a matching topology file,
* the resolver round-trips a mode id back to a readable topology file,
* the reserved namespace helpers behave the way the loader will depend on,
* the schema id pattern already refuses user ids under ``swarmkit:*`` (no new guard
  needed — this test documents the invariant).

Subsequent PRs wire the CLI shim and serve exposure against this scaffold.
"""

from __future__ import annotations

import jsonschema
import pytest
from swarmkit_runtime.authoring._prompts import get_system_prompt
from swarmkit_runtime.authoring._resolver import (
    AUTHORING_MODES,
    AUTHORING_NAMESPACE,
    get_authoring_workspace_path,
    is_authoring_id,
    is_reserved_namespace,
    resolve_authoring_topology,
)
from swarmkit_schema import validate


def test_bundled_workspace_resolves_to_a_real_directory() -> None:
    path = get_authoring_workspace_path()
    assert path.is_dir(), f"bundled authoring workspace missing: {path}"
    assert (path / "workspace.yaml").is_file()


@pytest.mark.parametrize("mode", AUTHORING_MODES)
def test_every_advertised_mode_ships_a_topology_file(mode: str) -> None:
    topology_file = get_authoring_workspace_path() / "topologies" / f"{mode}.yaml"
    assert topology_file.is_file(), f"missing bundled topology: {topology_file}"


@pytest.mark.parametrize("mode", AUTHORING_MODES)
def test_resolver_round_trips_a_mode_id(mode: str) -> None:
    topology_id = f"{AUTHORING_NAMESPACE}{mode}"
    assert is_authoring_id(topology_id)
    assert is_reserved_namespace(topology_id)
    resolved = resolve_authoring_topology(topology_id)
    assert resolved.is_file()


def test_resolver_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unknown authoring mode"):
        resolve_authoring_topology(f"{AUTHORING_NAMESPACE}nonsense")


def test_resolver_rejects_non_authoring_id() -> None:
    with pytest.raises(ValueError, match="not an authoring topology id"):
        resolve_authoring_topology("my-regular-topology")


def test_regular_id_is_not_authoring() -> None:
    assert not is_authoring_id("my-topology")
    assert not is_reserved_namespace("my-topology")


def test_reserved_namespace_is_broader_than_authoring() -> None:
    # swarmkit:* as a whole is reserved so future bundled surfaces can carve out
    # sub-namespaces without a schema change. Authoring is the first; the invariant
    # applies to any swarmkit: prefix.
    assert is_reserved_namespace("swarmkit:future-thing:x")
    assert not is_authoring_id("swarmkit:future-thing:x")


def test_schema_already_refuses_reserved_namespace_ids() -> None:
    # Validate a user topology that tries to register a swarmkit: prefixed id and prove
    # the canonical schema already refuses it. This is the structural guard — no
    # additional check is needed at load time because the schema validator catches it.
    # If this ever starts passing, the bundled workspace's uniqueness guarantee needs an
    # explicit Python-side check (and this test should flip to assert the explicit guard
    # exists).
    reserved = {
        "apiVersion": "swarmkit/v1",
        "kind": "Topology",
        "metadata": {"name": "swarmkit:author:topology", "version": "0.1.0"},
        "agents": {"root": {"id": "root", "role": "root", "archetype": "topology-author"}},
    }
    with pytest.raises(jsonschema.ValidationError):
        validate("topology", reserved)

    # Sanity: a legal id with the same shape validates (so the failure above really is
    # the colon, not something else about the fixture).
    legal = {**reserved, "metadata": {"name": "regular-topology", "version": "0.1.0"}}
    validate("topology", legal)


# ----- Prompt port (PR 2) ------------------------------------------------------------
# The authoring charter now lives in YAML (agents.root.prompt.system on each bundled
# topology). These cases confirm every mode loads, every mode produces the composed
# core + mode-specific + CLI-facts block, and dynamic workspace_context still appends
# the way the legacy loop expects.

# Expected prompt lengths captured right after the port — a cheap regression guard. If
# an operator intentionally rewrites a bundled prompt and the length moves, update the
# number here with the new one in the same commit.
_EXPECTED_PROMPT_LEN: dict[str, int] = {
    "init": 10683,
    "topology": 9681,
    "skill": 13583,
    "archetype": 7947,
    "mcp-server": 11013,
}


@pytest.mark.parametrize("mode", AUTHORING_MODES)
def test_mode_prompt_loads_from_yaml(mode: str) -> None:
    prompt = get_system_prompt(mode)  # type: ignore[arg-type]
    assert prompt, f"empty prompt for mode {mode}"
    assert "SwarmKit authoring assistant" in prompt, (
        "every mode's prompt starts with the shared core instructions — the port is "
        "composed, not mode-only"
    )
    assert "swarmkit run <workspace-dir>" in prompt, (
        "every mode's prompt ends with the CLI facts footer"
    )


@pytest.mark.parametrize("mode,expected_len", list(_EXPECTED_PROMPT_LEN.items()))
def test_mode_prompt_length_is_pinned(mode: str, expected_len: int) -> None:
    prompt = get_system_prompt(mode)  # type: ignore[arg-type]
    assert len(prompt) == expected_len, (
        f"{mode} prompt length changed from {expected_len} to {len(prompt)}; update "
        "_EXPECTED_PROMPT_LEN in this test if the change is intentional"
    )


def test_workspace_context_is_appended() -> None:
    base = get_system_prompt("topology")
    with_ctx = get_system_prompt("topology", workspace_context="existing: thing")
    assert with_ctx.endswith("Existing workspace state:\nexisting: thing")
    assert with_ctx.startswith(base)
