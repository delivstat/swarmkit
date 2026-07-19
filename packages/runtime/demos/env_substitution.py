"""Demo: artifact env-variable substitution (design/details/artifact-env-substitution.md).

Any string in any artifact can reference the environment: ${ENV_VAR}, ${VAR:-default}, and
$${VAR} (escape). Resolution order per ${NAME}: workspace property map -> OS environment ->
:-default -> left literal. Runs the real resolver, no model calls:

    uv run python packages/runtime/demos/env_substitution.py
"""

from __future__ import annotations

import os

from swarmkit_runtime.resolver._env_config import interpolate_dict


def show(label: str, artifact: dict, properties: dict[str, str] | None = None) -> None:
    out = interpolate_dict(artifact, properties or {})
    print(f"  {label}")
    print(f"    in : {artifact}")
    print(f"    out: {out}\n")


def main() -> None:
    print("Artifact env substitution — ${VAR}, ${VAR:-default}, $${VAR} escape\n")

    os.environ.pop("DEMO_MODEL", None)
    show(
        "1. default used when the var is unset",
        {"model": {"name": "${DEMO_MODEL:-deepseek/deepseek-v3}"}},
    )

    os.environ["DEMO_MODEL"] = "anthropic/claude-sonnet-5"
    show(
        "2. env var overrides the default",
        {"model": {"name": "${DEMO_MODEL:-deepseek/deepseek-v3}"}},
    )

    show(
        "3. workspace property map wins over env (two-level config)",
        {"model": {"provider": "${model.reasoning.provider}"}},
        {"model.reasoning.provider": "openrouter"},
    )

    show(
        "4. $${VAR} escapes to a literal ${VAR}",
        {"note": "$${NOT_A_VAR} stays literal"},
    )

    os.environ.pop("DEMO_MISSING", None)
    show(
        "5. unresolved ref with no default is left literal",
        {"x": "${DEMO_MISSING}"},
    )

    print("✓ env-substitution demo complete")


if __name__ == "__main__":
    main()
