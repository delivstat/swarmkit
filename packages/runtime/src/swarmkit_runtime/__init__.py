"""SwarmKit runtime — topology interpreter, LangGraph compiler, governance wiring.

See `design/SwarmKit-Design-v0.6.md` §9 and §14 for architectural context.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

#: Read from installed metadata, never a literal. This sat at "0.0.1" through 1.201 releases — the
#: precise drift `_versions.py` was written to warn about ("Metadata, never literals"), one import
#: away from the module that says so. Empty-ish default for an uninstalled source checkout.
try:
    __version__ = _version("swarmkit-runtime")
except PackageNotFoundError:  # pragma: no cover — running from a source tree, not an install
    __version__ = "0.0.0"

__all__ = ["__version__"]
