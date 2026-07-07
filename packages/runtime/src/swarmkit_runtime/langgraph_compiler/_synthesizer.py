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
from ._state import DEFAULT_SYNTHESIZER_ROLE, SynthesisConfig

_DEFAULT_SYNTHESIS_PROMPT = """\
You are a technical document writer. You have been given ALL research \
findings from specialist agents AND a scope contract that contains \
the architect's solution design. Your job is to structure this into \
a complete, review-ready document.

SCOPE CONTRACT — THE AUTHORITATIVE DESIGN:
- The SCOPE CONTRACT section below contains the architect's reasoned \
solution. The `solution_approach` entries ARE the design — each names \
a component, describes the change, and explains the rationale.
- Use solution_approach as the primary source for Solution Approach, \
Implementation Steps, and design decision sections.
- Use `open_questions` to flag unresolved items in the document.
- Use requirements/constraints/exclusions for the Scope section.

GROUNDING RULES:
- All factual claims (names, codes, configurations, ticket numbers, \
class names) must come from the research findings or scope contract.
- Do NOT invent identifiers that aren't in the findings.
- If a finding has a [source: ...] tag, preserve that attribution.
- If findings contradict each other, note the contradiction.

STRUCTURAL RULES:
- Follow the template structure exactly if one is provided.
- For diagram sections (sequence diagrams, state diagrams, flow \
diagrams, architecture diagrams), generate mermaid code blocks \
derived from the research findings. Use ```mermaid fenced blocks.
- For sections where the research provides partial data, synthesize \
what you can from the findings and note gaps explicitly.
- Only write 'To be determined during detailed design' when the \
findings provide absolutely zero relevant data for a section.

MERMAID SYNTAX (strict — invalid syntax won't render):
- Node IDs: alphanumeric + underscores only. No spaces or parens. \
Use bracket labels: `A[Label Text]` not `A(Label Text)`.
- Do NOT use `style` on subgraph names. Only style node IDs.
- sequenceDiagram: no `break` keyword. Use `Note` instead. Keep \
participant aliases short, use `as` for display names.
- stateDiagram-v2: max 1 level of nesting. Every node in a \
transition must be defined. No undefined references.
- Max ~20 nodes per diagram. Split complex flows into multiple \
diagrams. No CSS colors or complex styling — keep clean.

QUALITY RULES:
- Every section should add value. Prefer a derived diagram or \
inferred flow over a blank section.
- Cross-reference findings from different agents to build complete \
pictures (e.g., combine pipeline data with code analysis to \
produce sequence diagrams).
- Be specific and technical. Cite sources inline.\
"""


async def run_synthesis(
    config: SynthesisConfig,
    workspace_root: Path | None,
    original_input: str,
    provider_registry: ProviderRegistry | None,
    synthesizer_role: str = DEFAULT_SYNTHESIZER_ROLE,
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
    if template_path:
        _progress(f"[synthesizer] template: {template_path}")
    if output_path:
        _progress(f"[synthesizer] output: {output_path}")
    if not output_path:
        output_path = str(run_state / "synthesis-output.md")
        _progress(f"[synthesizer] no output path specified, writing to {output_path}")
    template = _load_file(template_path, root) if template_path else ""

    synthesis_prompt = config.prompt or _DEFAULT_SYNTHESIS_PROMPT
    prompt_parts = []
    prompt_parts.append(synthesis_prompt)

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
        synthesizer_role=synthesizer_role,
    )

    if output_path:
        _write_output(output_path, text, root)

    _progress(f"[synthesizer] done. Output: {len(text):,} chars.")
    return text


def _get_run_state(workspace_root: Path | None) -> Path:
    from ._run_context import run_state_dir  # noqa: PLC0415

    return run_state_dir(workspace_root)


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
    - Output: output/solution-design.md
    - template=review-docs/HLD_Template.md
    - in the format of review-docs/HLD_Template.md
    - following review-docs/HLD_Template.md format
    """
    _ext = r"\.(?:md|docx|txt|html)"
    patterns = [
        rf"(?i){label}\s*[:=]\s*(\S+{_ext})",
        rf"(?i){label}\s*[:=]\s*[\"']([^\"']+)[\"']",
    ]
    if label == "template":
        _fmt = r"(?:in the |in |follow(?:ing)? (?:the )?)"
        patterns.extend(
            [
                rf"(?i){_fmt}format (?:of |as )?(\S+{_ext})",
                rf"(?i)(?:format|structure) (?:of|from|per) (\S+{_ext})",
                rf"(?i)(?:based on|using|per) (\S+{_ext})",
            ]
        )
    for pattern in patterns:
        match = re.search(pattern, input_text)
        if match:
            return match.group(1).strip().rstrip(".")
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
    synthesizer_role: str = DEFAULT_SYNTHESIZER_ROLE,
) -> None:
    """Record synthesizer call in the active run trace."""

    from swarmkit_runtime.langgraph_compiler._compiler import get_active_trace  # noqa: PLC0415
    from swarmkit_runtime.trace import AgentStep  # noqa: PLC0415

    trace = get_active_trace()
    if trace is None:
        return

    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0

    step = AgentStep(
        agent_id="__synthesizer__",
        model=model,
        parent_agent=None,
        role=synthesizer_role,
        start_time=start_time,
        end_time=start_time + elapsed,
        duration_ms=int(elapsed * 1000),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        result_length=result_length,
    )
    trace.add_step(step)

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
