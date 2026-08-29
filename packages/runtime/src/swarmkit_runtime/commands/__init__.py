"""Command packs — local commands as a skill implementation type.

The sibling of ``mcp_servers`` for capabilities that already exist as binaries. See
``design/details/command-packs.md``.
"""

from swarmkit_runtime.commands._config import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    BinaryRequirement,
    CommandPackConfig,
    CommandPackError,
    CommandSpecConfig,
    check_requirements,
    parse_command_packs,
)
from swarmkit_runtime.commands._governed import (
    action_for,
    audit_payload,
    check_command_permission,
)
from swarmkit_runtime.commands._runner import (
    CommandExecutionError,
    CommandResult,
    build_argv,
    resolve_env,
    run_command,
)

__all__ = [
    "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "BinaryRequirement",
    "CommandExecutionError",
    "CommandPackConfig",
    "CommandPackError",
    "CommandResult",
    "CommandSpecConfig",
    "action_for",
    "audit_payload",
    "build_argv",
    "check_command_permission",
    "check_requirements",
    "parse_command_packs",
    "resolve_env",
    "run_command",
]
