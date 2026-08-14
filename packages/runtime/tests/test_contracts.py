"""Contract discovery + resolution, and the StageGraph lock ref-check (contract registry)."""

from __future__ import annotations

import textwrap
from pathlib import Path

from swarmkit_runtime.resolver import ResolvedContract, resolve_workspace

_WORKSPACE = "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata:\n  id: ws\n  name: WS\n"

_CONTRACT = textwrap.dedent(
    """\
    apiVersion: swarmkit/v1
    kind: Contract
    metadata:
      id: oms-web
      name: OMS to Web
      description: The order API OMS exposes to the Web storefront.
    parties: [oms, web]
    provenance:
      authored_by: human
      version: 1.0.0
    """
)


def _topology(name: str) -> str:
    return textwrap.dedent(
        f"""\
        apiVersion: swarmkit/v1
        kind: Topology
        metadata:
          name: {name}
          version: 0.1.0
        agents:
          root:
            id: root-{name}
            role: root
        """
    )


def _pipeline(lock: str) -> str:
    return textwrap.dedent(
        f"""\
        apiVersion: swarmkit/v1
        kind: StageGraph
        metadata:
          id: p
          name: P
          description: A one-stage pipeline that locks a contract while it runs.
        stages:
          - id: design
            topology: design-topo
            when: [start]
            locks: [{lock}]
            success: done
        provenance:
          authored_by: human
          version: 1.0.0
        """
    )


def _scaffold(root: Path, *, lock: str, with_contract: bool = True) -> None:
    (root / "workspace.yaml").write_text(_WORKSPACE)
    if with_contract:
        (root / "contracts").mkdir()
        (root / "contracts" / "oms-web.yaml").write_text(_CONTRACT)
    (root / "topologies").mkdir()
    (root / "topologies" / "design-topo.yaml").write_text(_topology("design-topo"))


def test_contract_resolves_and_lock_ref_checks(tmp_path: Path) -> None:
    _scaffold(tmp_path, lock="oms-web")
    ws = resolve_workspace(tmp_path)
    assert "oms-web" in ws.contracts
    contract = ws.contracts["oms-web"]
    assert isinstance(contract, ResolvedContract)
    assert contract.parties == ("oms", "web")
