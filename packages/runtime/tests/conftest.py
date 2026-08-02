"""Shared test helpers.

The one thing here exists because tests copied the example workspace *including* its local
``.swarmkit/`` directory — whatever runs, audit rows and SQLite files happened to be sitting in
the developer's tree. That made every such test depend on machine state, and under ``-n auto`` it
also raced: one worker copying the directory while another wrote to it fails with ``shutil.Error``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

#: Runtime state, never inputs. Copying these makes a test inherit whatever the last local run
#: left behind — which is how a demo once reported 3,465 rows in a "fresh" workspace.
_RUNTIME_STATE = shutil.ignore_patterns(".swarmkit", "__pycache__", "*.pyc")


def copy_workspace(src: Path, dest: Path) -> Path:
    """Copy a workspace's ARTIFACTS to *dest*, leaving its runtime state behind."""
    shutil.copytree(src, dest, ignore=_RUNTIME_STATE)
    return dest
