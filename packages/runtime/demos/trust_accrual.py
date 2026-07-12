"""Demo: trust accrual → allowlist changeset (executor-trust-accrual-plan.md, RFC §6.2.3).

Relay makes a human approve an out-of-grant capability *every* run. Trust accrual watches those
approvals per (archetype, capability) and, after enough consecutive ones with no denial, proposes
adding the capability to the archetype's allowlist. The operator applies it once — and future runs
stop asking. A single denial resets the count and blocks the pair until it is cleared.

This runs the real store + the real `swarmkit trust` CLI against a throwaway workspace (no model
call, no API budget):

  1. Five operator approvals of `coding-worker` + `Bash(npm test)` — a proposal appears at N=5.
  2. `swarmkit trust apply` edits the archetype's `executor.config.allowed_tools` — the grant.
  3. A single denial of another capability blocks it — even reaching N proposes nothing until clear.

Run it:

    uv run python packages/runtime/demos/trust_accrual.py
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import yaml
from swarmkit_runtime.trust import TrustStore

ARCHETYPE = "coding-worker"
CAP = "Bash(npm test)"


def _bar(label: str) -> None:
    print(f"\n--- {label}")


def _cli(*args: str) -> str:
    out = subprocess.run(
        ["swarmkit", "trust", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _write_archetype(root: Path) -> Path:
    directory = root / "archetypes"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{ARCHETYPE}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "swarmkit/v1",
                "kind": "Archetype",
                "metadata": {"id": ARCHETYPE, "name": "Coding Worker"},
                "role": "worker",
                "defaults": {},
                "executor": {
                    "kind": "claude-code",
                    "ref": "claude-code",
                    "config": {"allowed_tools": "Read, Edit"},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        arch = _write_archetype(root)
        store = TrustStore(root, threshold=5)

        _bar("1. Operator approves the same relayed capability, run after run")
        for i in range(1, 6):
            proposal = store.record(ARCHETYPE, CAP, granted=True)
            flag = "  → PROPOSED (crossed N=5)" if proposal else ""
            print(f"  approval {i}: {ARCHETYPE} + {CAP}{flag}")

        _bar("2. `swarmkit trust list` surfaces the pending changeset")
        print(_cli("list", str(root)))

        _bar("3. `swarmkit trust apply` records the grant in the archetype's allowlist")
        print(_cli("apply", ARCHETYPE, CAP, str(root)))
        allowed = yaml.safe_load(arch.read_text())["executor"]["config"]["allowed_tools"]
        print(f"  allowed_tools is now: {allowed!r}")
        print(f"  pending proposals after apply: {store.proposals()}")

        _bar("4. A single denial blocks a different capability — one 'no' is a signal, not noise")
        store.record(ARCHETYPE, "Bash(rm -rf)", granted=False)  # deliberate refusal
        for _ in range(5):
            store.record(ARCHETYPE, "Bash(rm -rf)", granted=True)
        print(f"  after a denial + 5 approvals, proposals: {store.proposals()}  (blocked)")
        print("  clearing the block:")
        print("   ", _cli("clear", ARCHETYPE, "Bash(rm -rf)", str(root)))

    print("\nOK — accrue → propose → apply (human grant); denial blocks until cleared.")


if __name__ == "__main__":
    main()
