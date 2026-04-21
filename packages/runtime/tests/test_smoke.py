"""Smoke tests — verify the package imports and the CLI wires up."""

from __future__ import annotations

import pytest
import swarmkit_runtime
from swarmkit_runtime.cli import app
from swarmkit_runtime.governance import GovernanceProvider


def test_package_imports() -> None:
    assert swarmkit_runtime.__version__


def test_cli_app_constructible() -> None:
    assert app.info.name == "swarmkit"


def test_governance_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        GovernanceProvider()  # type: ignore[abstract]
