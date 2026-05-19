"""Automatic synthesizer — single large-context call for document generation.

When all tasks are complete and scope exists, the compiler invokes this
instead of giving the architect a "synthesize" checkpoint. Loads ALL raw
result files, scope, and template into one model call.

Template and output paths are extracted from the user's original input
prompt — different requests can use different templates. The synthesizer
writes the output file to disk and returns the content.
"""

from __future__ import annotations

import re
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
    """Run the synthesizer — one large-context call with all results."""
    import json  # noqa: PLC0415

    _progress("[synthesizer] loading all results for single-context synthesis...")

    root = workspace_root or Path(".")
    run_state = _get_run_state(workspace_root)
    results = _load_all_results(run_state)
    scope = _load_scope(run_state)
    template_path = _extract_path(original_input, "template")
    output_path = _extract_path(original_input, "output")
    template = _load_file(template_path, root) if template_path else ""

    prompt_parts = []
    prompt_parts.append(
        "You are a technical document writer. You have been given ALL research "
        "findings from multiple specialist agents. Your job is to synthesize "
        "these into a single coherent solution design document.\n\n"
        "CRITICAL RULES:\n"
        "- Use ONLY the data provided below. Do NOT add information from "
        "your training data.\n"
        "- If a finding has a [source: ...] tag, preserve that attribution.\n"
        "- If findings contradict each other, note the contradiction.\n"
        "- Follow the template structure exactly if one is provided.\n"
        "- Every claim must be traceable to a specific finding below.\n"
        "- If the findings don't cover a section, write 'NOT COVERED IN "
        "RESEARCH' rather than fabricating content.\n"
        "- Do NOT invent class names, service names, Jira tickets, or "
        "OrderType codes that aren't in the findings.\n"
    )

    if template:
        prompt_parts.append(f"\n## TEMPLATE (follow this structure exactly):\n\n{template}\n")

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

    import time  # noqa: PLC0415

    _start = time.time()
    provider = _resolve_provider(config, provider_registry)
    response = await provider.complete(
        CompletionRequest(
            model=config.model,
            messages=(Message(role="user", content=full_prompt),),
            system=None,
        )
    )
    _elapsed = time.time() - _start

    text = response.text or "(synthesis produced no output)"

    _record_trace(
        model=config.model,
        usage=response.usage,
        start_time=_start,
        elapsed=_elapsed,
        result_length=len(text),
    )

    if output_path:
        _write_output(output_path, text, root)

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


def _extract_path(input_text: str, label: str) -> str:
    """Extract a file path from input text by label.

    Looks for patterns like:
    - Template: review-docs/HLD_Template.md
    - Output: output/RT-727_HLD.md
    - template=review-docs/HLD_Template.md
    """
    patterns = [
        rf"(?i){label}\s*[:=]\s*(\S+\.(?:md|docx|txt|html))",
        rf"(?i){label}\s*[:=]\s*[\"']([^\"']+)[\"']",
    ]
    for pattern in patterns:
        match = re.search(pattern, input_text)
        if match:
            return match.group(1).strip()
    return ""


def _load_file(path_str: str, root: Path) -> str:
    """Load a file relative to workspace root."""
    path = root / path_str
    if not path.exists():
        _progress(f"[synthesizer] file not found: {path}")
        return ""
    return path.read_text(encoding="utf-8")


def _write_output(path_str: str, content: str, root: Path) -> None:
    """Write synthesized document to disk."""
    path = root / path_str
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _progress(f"[synthesizer] wrote document to {path}")


def _record_trace(
    model: str,
    usage: Any,
    start_time: float,
    elapsed: float,
    result_length: int,
) -> None:
    """Record synthesizer call in the active run trace."""

    from swarmkit_runtime.langgraph_compiler._compiler import _active_trace  # noqa: PLC0415
    from swarmkit_runtime.trace import AgentStep  # noqa: PLC0415

    if _active_trace is None:
        return

    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0

    step = AgentStep(
        agent_id="__synthesizer__",
        model=model,
        parent_agent=None,
        role="synthesizer",
        start_time=start_time,
        end_time=start_time + elapsed,
        duration_ms=int(elapsed * 1000),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        result_length=result_length,
    )
    _active_trace.add_step(step)

    _progress(
        f"[synthesizer] tokens: {input_tokens:,} in / {output_tokens:,} out / "
        f"{input_tokens + output_tokens:,} total ({elapsed:.1f}s)"
    )


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
