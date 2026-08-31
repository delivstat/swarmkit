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
- `swarmkit-webui` (NOT caught, shipped, reported by a user): 0.14.0 was cut 2026-08-07 and the
  bundled pipeline was removed from the runtime a week later. Six commits changed the UI and the
  version never moved, so the published bundle still shipped a Pipelines section navigating to an
  API that no longer exists — a whole dead area of the portal, and no version to upgrade to.

**Both misses have one cause: the baseline was the last tag.** A version frozen across several
releases stops looking changed the moment its change falls out of that one-tag window, which is
precisely the situation this is meant to catch. The baseline is now the commit that last SET the
version — so a package is compared against its own release point, however long ago that was.

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
#:
#: The pyproject is itself watched content: dependencies, extras and entry points ship in the wheel
#: metadata, so tightening a version floor is a change users need and the old list could not see.
#: Its own version line is not a false positive — the baseline is the commit that SET the version,
#: and `git diff <commit>..HEAD` excludes that commit, so only LATER edits count.
PACKAGES = [
    (
        "swarmkit-runtime",
        "packages/runtime/pyproject.toml",
        ["packages/runtime/src", "packages/runtime/pyproject.toml"],
    ),
    (
        "swarmkit-schema",
        "packages/schema/python/pyproject.toml",
        ["packages/schema/schemas", "packages/schema/python/pyproject.toml"],
    ),
    (
        "swarmkit-webui",
        "packages/webui/pyproject.toml",
        [
            "packages/ui/app",
            "packages/ui/lib",
            "packages/ui/components",
            "packages/webui/pyproject.toml",
        ],
    ),
    (
        "swarmkit-control-plane",
        "packages/control-plane/pyproject.toml",
        ["packages/control-plane/src", "packages/control-plane/pyproject.toml"],
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


#: Suffixes whose content never reaches a built artifact. A test beside the source it tests lives
#: inside a package directory but is excluded from the wheel and from the Next.js production build,
#: so counting it as a change makes the guard demand a version bump that would publish a
#: byte-identical release. The guard exists to stop a real change being silently skipped; a false
#: alarm that costs a permanent PyPI version is the opposite failure.
_NOT_SHIPPED = (".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", "_test.py")


def _ships(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return not (name.startswith("test_") or path.endswith(_NOT_SHIPPED))


def _changed_since(tag: str, paths: list[str]) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{tag}..HEAD", "--", *paths],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in out.stdout.splitlines() if line.strip() and _ships(line)]


def _version_set_at(pyproject: str) -> str | None:
    """The commit that last changed the ``version`` line in this pyproject.

    This, not the last tag, is the package's own release point. Comparing against the last tag asks
    "did this change recently?", which a version frozen for five releases always answers no to. The
    question that matters is "has anything shipped in this package changed since the version
    currently on PyPI was set?"
    """
    out = subprocess.run(
        ["git", "log", "-1", "--format=%H", "-L", "/^version = /,+1:" + pyproject],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    head = out.stdout.strip().splitlines()
    if out.returncode == 0 and head:
        return head[0].strip()
    # `git log -L` needs a matching line; a pyproject without one is a packaging error, not ours.
    return None


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
    print(f"last tag {_last_tag()}; each package compared against its own version commit\n")
    problems: list[str] = []
    for name, pyproject, sources in PACKAGES:
        version = _local_version(pyproject)
        baseline = _version_set_at(pyproject) or _last_tag()
        changed = _changed_since(baseline, sources)
        already = version in _published(name)
        state = "would SKIP (already on PyPI)" if already else "would publish"
        mark = " "
        if changed and already:
            mark = "!"
            problems.append(
                f"{name}: {len(changed)} file(s) changed since version {version} was set "
                f"({baseline[:9]}) and {version} is already published — the change will not reach "
                f"anyone. Bump {pyproject}.\n      first few: " + ", ".join(changed[:4])
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
