#!/usr/bin/env python
"""Fail if a package's content changed but its version did not.

    uv run python scripts/check_publishable.py

The publish workflow uses `publish_if_new`: it uploads a package only when its version is not
already on PyPI. So a package whose version did not move is skipped — silently, with every workflow
green and the release reporting success.

That has bitten twice:

- `swarmkit-webui` (caught before tagging v1.145.0): the jobs-history page was in the rebuilt bundle
  while the wheel version was already published.
- `swarmkit-schema` (NOT caught, shipped): the version sat at 1.23.0 from 2026-07-27 through six
  releases, so `server.auth.config.identity`, `client_id`, `scope`, `storage.artifacts` and the
  declarative adapters' `*_map` tables were all unreachable by anyone who installed SwarmKit — the
  runtime shipped adapters that its own published schema rejected.

A rule you have to remember is not a rule. This is the executable version.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: (pypi name, pyproject path, the source dirs whose content ends up in that package)
PACKAGES = [
    ("swarmkit-runtime", "packages/runtime/pyproject.toml", ["packages/runtime/src"]),
    ("swarmkit-schema", "packages/schema/python/pyproject.toml", ["packages/schema/schemas"]),
    (
        "swarmkit-webui",
        "packages/webui/pyproject.toml",
        ["packages/ui/app", "packages/ui/lib", "packages/ui/components"],
    ),
    (
        "swarmkit-control-plane",
        "packages/control-plane/pyproject.toml",
        ["packages/control-plane/src"],
    ),
]


def _local_version(pyproject: str) -> str:
    data = tomllib.loads((REPO / pyproject).read_text())
    return str(data["project"]["version"])


def _published(name: str) -> set[str]:
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=20) as r:
            return set(json.load(r)["releases"])
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return set()  # never published — anything is new
        raise


def _changed_since(tag: str, paths: list[str]) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{tag}..HEAD", "--", *paths],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def _last_tag() -> str:
    out = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0", "--match", "v1.*"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def main() -> int:
    tag = _last_tag()
    print(f"comparing against {tag}\n")
    problems: list[str] = []
    for name, pyproject, sources in PACKAGES:
        version = _local_version(pyproject)
        changed = _changed_since(tag, sources)
        already = version in _published(name)
        state = "would SKIP (already on PyPI)" if already else "would publish"
        mark = " "
        if changed and already:
            mark = "!"
            problems.append(
                f"{name}: {len(changed)} file(s) changed since {tag} but version {version} is "
                f"already published — the change will not reach anyone. Bump {pyproject}."
            )
        print(f" {mark} {name:<24} {version:<10} {len(changed):>3} changed   {state}")

    if problems:
        print("\nFAIL")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nOK — every package with changes has a version that will publish.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
