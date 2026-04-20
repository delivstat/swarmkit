"""Smoke tests — verify the package imports and the CLI wires up."""

from __future__ import annotations


def test_package_imports() -> None:
    import swarmkit_runtime

    assert swarmkit_runtime.__version__


def test_cli_app_constructible() -> None:
    from swarmkit_runtime.cli import app

    assert app.info.name == "swarmkit"


def test_governance_provider_is_abstract() -> None:
    import pytest

    from swarmkit_runtime.governance import GovernanceProvider

    with pytest.raises(TypeError):
        GovernanceProvider()  # type: ignore[abstract]
