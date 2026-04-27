"""Smoke tests — verify the package imports and the CLI wires up."""

from __future__ import annotations

import pytest
import swael_runtime
from swael_runtime.cli import app
from swael_runtime.governance import GovernanceProvider


def test_package_imports() -> None:
    assert swael_runtime.__version__


def test_cli_app_constructible() -> None:
    assert app.info.name == "swarmkit"


def test_governance_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        GovernanceProvider()  # type: ignore[abstract]
