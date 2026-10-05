"""Author tool: read_workspace.

Stub — ports in #1045 PR-2 alongside the matching skill schema. For the foundation PR
this exists so the command_pack loads; it exits with 'not yet wired' until the shim
lands.
"""

from __future__ import annotations

import json
import sys


def main() -> int:
    sys.stdout.write(json.dumps({"error": "not yet wired — see #1045 PR-2"}))
    sys.stdout.write("\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
