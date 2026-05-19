"""Automatic synthesizer — single large-context call for document generation.

When all tasks are complete and scope exists, the compiler invokes this
instead of giving the architect a "synthesize" checkpoint. Loads ALL raw
result files, scope, and template into one model call.

This solves the multi-agent synthesis problem: workers find the right data,
but the architect can't cross-reference it because it only sees summaries.
The synthesizer sees everything in one context window.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from swarmkit_runtime.model_providers import CompletionRequest, Message
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol, ProviderRegistry

from ._helpers import _progress
from ._state import SynthesisConfig


async def run_synthesis(
    config: SynthesisConfig,
    workspace_root: Path | None,
    original_input: str,
    provider_registry: ProviderRegistry | None,
) -> str:
    """Run the synthesizer — one large-context call with all results.

    Loads:
    - All .md result files from .swarmkit/run-state/current/
    - scope.json
    - Template file (if configured)
    - Original user input

    Returns the synthesized document text.
    """
    import json  # noqa: PLC0415

    _progress("[synthesizer] loading all results for single-context synthesis...")

    run_state = _get_run_state(workspace_root)
    results = _load_all_results(run_state)
    scope = _load_scope(run_state)
    template = _load_template(config.template, workspace_root)

    prompt_parts = []
    prompt_parts.append(
        "You are a technical document writer. You have been given ALL research "
        "findings from multiple specialist agents. Your job is to synthesize "
        "these into a single coherent solution design document.\n\n"
        "CRITICAL RULES:\n"
        "- Use ONLY the data provided below. Do NOT add information from your training.\n"
        "- If a finding has a [source: ...] tag, preserve that attribution.\n"
        "- If findings contradict each other, note the contradiction explicitly.\n"
        "- Follow the template structure exactly if one is provided.\n"
        "- Every claim must be traceable to a specific finding below.\n"
        "- If the findings don't cover a section, write 'NOT COVERED IN RESEARCH' "
        "rather than fabricating content.\n"
    )

    if template:
        prompt_parts.append(f"\n## TEMPLATE (follow this structure):\n\n{template}\n")

    if scope:
        prompt_parts.append(f"\n## SCOPE CONTRACT:\n\n{json.dumps(scope, indent=2)}\n")

    prompt_parts.append(f"\n## ORIGINAL REQUEST:\n\n{original_input}\n")

    prompt_parts.append("\n## RESEARCH FINDINGS:\n")
    for filename, content in results.items():
        prompt_parts.append(f"\n### [{filename}]\n\n{content}\n")

    full_prompt = "\n".join(prompt_parts)

    _progress(
        f"[synthesizer] {len(results)} results, "
        f"{len(full_prompt):,} chars total context. "
        f"Calling {config.model}..."
    )

    provider = _resolve_provider(config, provider_registry)
    response = await provider.complete(
        CompletionRequest(
            model=config.model,
            messages=(Message(role="user", content=full_prompt),),
            system=None,
        )
    )

    text = response.text or "(synthesis produced no output)"
    _progress(f"[synthesizer] done. Output: {len(text):,} chars.")
    return text


def _get_run_state(workspace_root: Path | None) -> Path:
    if workspace_root:
        return workspace_root / ".swarmkit" / "run-state" / "current"
    return Path(".swarmkit/run-state/current")


def _load_all_results(run_state: Path) -> dict[str, str]:
    """Load all .md result files from run state."""
    results: dict[str, str] = {}
    if not run_state.exists():
        return results
    for path in sorted(run_state.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        if content.strip():
            results[path.name] = content
    return results


def _load_scope(run_state: Path) -> dict[str, Any] | None:
    """Load scope.json if it exists."""
    import json  # noqa: PLC0415

    scope_path = run_state / "scope.json"
    if not scope_path.exists():
        return None
    try:
        return json.loads(scope_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except (OSError, ValueError):
        return None


def _load_template(template_path: str, workspace_root: Path | None) -> str:
    """Load the template file if configured."""
    if not template_path:
        return ""
    root = workspace_root or Path(".")
    path = root / template_path
    if not path.exists():
        _progress(f"[synthesizer] template not found: {path}")
        return ""
    return path.read_text(encoding="utf-8")


def _resolve_provider(
    config: SynthesisConfig,
    registry: ProviderRegistry | None,
) -> ModelProviderProtocol:
    """Resolve the model provider for synthesis."""
    from swarmkit_runtime.model_providers import MockModelProvider  # noqa: PLC0415

    if registry is not None:
        provider = registry.get(config.provider)
        if provider is not None:
            return provider
    return MockModelProvider()
