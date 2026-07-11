"""Launch-block human-review gate for declarative adapters (executor-abstraction §5.2, P3 PR6).

The ``launch`` block of an adapter is the sharpest edge — it is a command line executed on the host.
So a **workspace-authored** adapter must have its launch surface **human-approved** before it can
run a harness, and **re-approved on any change** to that surface. This holds regardless of the
workspace's auto-run trust settings (RFC decision 6.2) and is a scope reserved for human identity
(CLAUDE.md invariant #6): approval is a human CLI action that writes an on-disk record — never
something an agent can grant.

Bundled reference adapters (shipped + vetted in the package) are pre-trusted and not gated; only a
workspace's own ``adapters/`` are.

The fingerprint covers the whole *executable surface* — the launch command, its optional-arg groups,
injected env, and the auth modes' contributed args/env — so a change anywhere that alters what runs
invalidates the approval.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from swarmkit_runtime.executors._adapter_spec import AdapterSpec

_APPROVALS_FILE = ".swarmkit/adapters-approved.json"


def launch_fingerprint(spec: AdapterSpec) -> str:
    """A stable SHA-256 over the adapter's executable surface (launch + auth contributions)."""
    launch = spec.launch
    surface = {
        "command": list(launch.command),
        "optional_args": [[o.when, list(o.args)] for o in launch.optional_args],
        "env": dict(sorted(launch.env.items())),
        "resume_arg": list(spec.resume_arg),
        "auth": {
            name: {
                "env": dict(sorted(m.env.items())),
                "args": list(m.args),
                "credential_paths": list(m.credential_paths),
            }
            for name, m in sorted(spec.auth.modes.items())
        },
    }
    blob = json.dumps(surface, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def _approvals_path(workspace_root: Path) -> Path:
    return workspace_root / _APPROVALS_FILE


def approved_launches(workspace_root: Path) -> dict[str, str]:
    """The approved ``{adapter_id: fingerprint}`` map for a workspace (empty if none)."""
    path = _approvals_path(workspace_root)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def is_launch_approved(workspace_root: Path, spec: AdapterSpec) -> bool:
    """True iff this adapter's *current* launch surface has a matching approval on record."""
    return approved_launches(workspace_root).get(spec.kind) == launch_fingerprint(spec)


def approve_launch(workspace_root: Path, spec: AdapterSpec) -> str:
    """Record approval of the adapter's current launch surface (a human action). Returns the
    fingerprint written."""
    path = _approvals_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    approvals = approved_launches(workspace_root)
    fingerprint = launch_fingerprint(spec)
    approvals[spec.kind] = fingerprint
    path.write_text(json.dumps(approvals, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return fingerprint
