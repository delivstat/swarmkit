"""Demo: run `swarmkit validate` against representative fixtures and print
the output a first-time user would actually see.

Used by `just demo-validate`. Exit criterion for tasks #23 + #31.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "packages" / "runtime" / "tests" / "fixtures"

CASES = [
    ("valid — full workspace (summary mode)", ["--no-color"], "workspaces/full"),
    (
        "valid — resolved-tree workspace (tree mode)",
        ["--tree", "--no-color"],
        "workspaces/resolved-tree",
    ),
    (
        "invalid — unknown archetype reference",
        ["--no-color"],
        "workspaces-invalid/unknown-archetype",
    ),
    (
        "invalid — abstract skill placeholder ambiguous",
        ["--no-color"],
        "workspaces-invalid/abstract-ambiguous",
    ),
    (
        "invalid — composed-skill cycle",
        ["--no-color"],
        "workspaces-invalid/composed-cycle",
    ),
    (
        "invalid — workspace.yaml missing",
        ["--no-color"],
        "workspaces-invalid/missing-workspace-yaml",
    ),
]


def main() -> int:
    all_ok = True
    for title, flags, rel_path in CASES:
        banner = f"── {title} " + "─" * max(1, 60 - len(title))
        print(banner)
        path = FIXTURES / rel_path
        cmd = ["uv", "run", "swarmkit", "validate", str(path), *flags]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        print(result.stdout.rstrip("\n"))
        if result.stderr:
            print(result.stderr.rstrip("\n"), file=sys.stderr)
        print(f"  exit: {result.returncode}")
        print()
        # Valid fixtures should exit 0; invalid should exit 1. Anything
        # else is a regression.
        expected = 0 if rel_path.startswith("workspaces/") else 1
        if result.returncode != expected:
            all_ok = False
            print(
                f"✗ unexpected exit {result.returncode} (expected {expected})",
                file=sys.stderr,
            )

    print("all cases passed." if all_ok else "one or more cases failed.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
