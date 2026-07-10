"""Skill execution for the compiler's agentic tool-use loop.

Supports ``llm_prompt``, ``mcp_tool``, and ``composed`` skills.
See ``design/details/decision-skills.md`` and ``design/details/mcp-client.md``.
"""

from __future__ import annotations

import json
from typing import Any

from swarmkit_runtime.governance import GovernanceProvider
from swarmkit_runtime.mcp._client import MCPClientManager
from swarmkit_runtime.model_providers import (
    CompletionRequest,
    ContentBlock,
    Message,
)
from swarmkit_runtime.model_providers._registry import ModelProviderProtocol
from swarmkit_runtime.skills import ResolvedSkill, impl_get
from swarmkit_runtime.telemetry import record_governance_decision

SkillResult = str | tuple[str, list[ContentBlock]]


async def execute_skill(
    skill: ResolvedSkill,
    *,
    input_text: str,
    model_provider: ModelProviderProtocol,
    model_name: str,
    mcp_manager: MCPClientManager | None = None,
    governance: GovernanceProvider | None = None,
    agent_id: str = "",
) -> SkillResult:
    """Execute a skill and return the result.

    Returns either a plain string or a ``(text, image_blocks)`` tuple
    when the MCP tool response includes image content.
    """
    impl = skill.raw.implementation
    impl_type = impl_get(impl, "type")

    if impl_type == "llm_prompt":
        return await _execute_llm_prompt(
            skill,
            input_text=input_text,
            model_provider=model_provider,
            model_name=model_name,
        )

    if impl_type == "mcp_tool":
        return await _execute_mcp_tool(
            skill,
            input_text=input_text,
            mcp_manager=mcp_manager,
            governance=governance,
            agent_id=agent_id,
        )

    if impl_type == "composed":
        return await _execute_composed(
            skill,
            input_text=input_text,
            model_provider=model_provider,
            model_name=model_name,
            mcp_manager=mcp_manager,
        )

    return f"[skill:{skill.id}] Unknown implementation type: {impl_type}"


async def _execute_llm_prompt(
    skill: ResolvedSkill,
    *,
    input_text: str,
    model_provider: ModelProviderProtocol,
    model_name: str,
) -> str:
    """Execute an ``llm_prompt`` skill by calling the model with its prompt."""
    impl = skill.raw.implementation
    prompt = impl_get(impl, "prompt")

    model_config = impl_get(impl, "model", None)
    if model_config and isinstance(model_config, dict):
        model_name = model_config.get("name", model_name)

    system_prompt = str(prompt) if prompt else f"You are executing the skill: {skill.id}"

    response = await model_provider.complete(
        CompletionRequest(
            model=model_name,
            messages=(Message(role="user", content=input_text),),
            system=system_prompt,
        )
    )

    parts = [b.text for b in response.content if b.type == "text" and b.text]
    return "\n".join(parts) or "(no response)"


async def _execute_mcp_tool(  # noqa: PLR0911, PLR0912, PLR0915
    skill: ResolvedSkill,
    *,
    input_text: str,
    mcp_manager: MCPClientManager | None,
    governance: GovernanceProvider | None = None,
    agent_id: str = "",
) -> SkillResult:
    """Execute an ``mcp_tool`` skill by calling the MCP server.

    If a ``GovernanceProvider`` is supplied, ``evaluate_action`` is called
    before the MCP tool invocation. The action string follows the
    ``mcp:call:<server>:<tool>`` convention from the design note
    (``design/details/mcp-client.md``).
    """
    impl = skill.raw.implementation
    server_id = str(impl_get(impl, "server"))
    tool_name = str(impl_get(impl, "tool"))

    permission = (
        mcp_manager.get_permission(server_id, tool_name) if mcp_manager is not None else "cautious"
    )

    if permission != "open" and governance is not None:
        iam = getattr(skill.raw, "iam", None)
        scopes: frozenset[str] = frozenset()
        if iam and isinstance(iam, dict):
            scopes = frozenset(iam.get("required_scopes", []))
        decision = await governance.evaluate_action(
            agent_id=agent_id,
            action=f"mcp:call:{server_id}:{tool_name}",
            scopes_required=scopes,
            context={"server_permission": permission},
        )
        record_governance_decision(
            decision="allow" if decision.allowed else "deny", scope="mcp:call"
        )
        if not decision.allowed:
            return f"[skill:{skill.id}] DENIED: {decision.reason}"

    if mcp_manager is None:
        return (
            f"[skill:{skill.id}] cannot call MCP tool '{tool_name}' on server "
            f"'{server_id}': workspace declares no mcp_servers. "
            f"Add an entry under `mcp_servers:` in workspace.yaml."
        )

    try:
        is_json = input_text.strip().startswith("{")
        arguments: dict[str, Any] = json.loads(input_text) if is_json else {"input": input_text}
    except (json.JSONDecodeError, TypeError):
        arguments = {"input": input_text}

    import os as _os  # noqa: PLC0415

    if _os.environ.get("SWARMKIT_VERBOSE"):
        import sys as _sys  # noqa: PLC0415

        print(f"  [mcp args: {arguments}]", file=_sys.stderr)

    # Sanitise path arguments — models often send absolute paths that the
    # filesystem MCP server rejects.  Try to convert to a relative path
    # within the server's cwd; fall back to "." only as a last resort.
    import os as _os  # noqa: PLC0415

    server_cwd = mcp_manager.get_server_cwd(server_id) if mcp_manager else None

    for path_key in ("path", "directory", "root", "rootPath"):
        if path_key in arguments and isinstance(arguments[path_key], str):
            path_val = arguments[path_key]
            is_absolute = path_val.startswith("/") or path_val.startswith("\\")
            if is_absolute and server_cwd and path_val.startswith(server_cwd):
                suffix = path_val[len(server_cwd) :]
                relative = suffix.lstrip("/") or "."
                arguments[path_key] = relative
                if _os.environ.get("SWARMKIT_VERBOSE"):
                    import sys as _sys  # noqa: PLC0415

                    print(
                        f"  [path sanitised: {path_val} → {relative}]",
                        file=_sys.stderr,
                    )

    # Check tool result cache before making the call.
    # Skip cache for side-effect tools (downloads, writes, creates, etc.)
    _nocache_signals = (
        "download",
        "write",
        "create",
        "delete",
        "update",
        "send",
        "upload",
        "add",
        "remove",
    )
    tool_lower = tool_name.lower()
    cacheable = not any(sig in tool_lower for sig in _nocache_signals)

    if cacheable:
        cached = mcp_manager.get_cached_result(server_id, tool_name, arguments)
        if cached is not None:
            mcp_manager._cache_hits += 1
            if _os.environ.get("SWARMKIT_VERBOSE"):
                import sys as _sys  # noqa: PLC0415

                print(f"  [cache hit: {tool_name}]", file=_sys.stderr)
            return cached

    mcp_manager._cache_misses += 1

    import asyncio as _asyncio  # noqa: PLC0415

    _timeout = int(_os.environ.get("SWARMKIT_MCP_TIMEOUT", "180"))
    _max_retries = int(_os.environ.get("SWARMKIT_MCP_RETRIES", "2"))

    tool_response = None
    for _attempt in range(_max_retries + 1):
        try:
            tool_response = await _asyncio.wait_for(
                mcp_manager.call_tool(server_id, tool_name, arguments),
                timeout=_timeout,
            )
            break
        except TimeoutError:
            if _os.environ.get("SWARMKIT_VERBOSE"):
                import sys as _sys  # noqa: PLC0415

                print(
                    f"  [timeout: {tool_name} attempt {_attempt + 1}/{_max_retries + 1}]",
                    file=_sys.stderr,
                )
            if _attempt == _max_retries:
                total = _max_retries + 1
                return f"[skill:{skill.id}] MCP call timed out after {_timeout}s x {total} attempts"
        except LookupError as exc:
            return f"[skill:{skill.id}] {exc}"
        except Exception as exc:
            if _attempt == _max_retries:
                return f"[skill:{skill.id}] MCP call failed: {exc}"
            if _os.environ.get("SWARMKIT_VERBOSE"):
                import sys as _sys  # noqa: PLC0415

                print(
                    f"  [retry: {tool_name} attempt {_attempt + 1} failed: {exc}]",
                    file=_sys.stderr,
                )

    if tool_response is None:
        return f"[skill:{skill.id}] MCP call failed after {_max_retries + 1} attempts"

    result = tool_response.data
    metadata = tool_response.metadata

    text_parts: list[str] = []
    image_blocks: list[ContentBlock] = []
    if result.content:
        for block in result.content:
            text = getattr(block, "text", None)
            if text:
                text_parts.append(text)
            if getattr(block, "type", None) == "image":
                image_blocks.append(
                    ContentBlock(
                        type="image",
                        image_data=getattr(block, "data", None),
                        image_media_type=getattr(block, "mimeType", None),
                    )
                )

    # Text content is primary: FastMCP and most servers serialise the result
    # (including structured output) into a text block, so this carries the
    # meaningful payload. Only when a server returns structured output with no
    # text fallback — permitted by the MCP spec — do we surface
    # ``structuredContent`` directly, so such tools no longer read as empty.
    text_output = "\n".join(text_parts)
    if text_output:
        output = text_output
    else:
        structured = getattr(result, "structuredContent", None)
        if structured:
            import json as _json  # noqa: PLC0415

            output = _json.dumps(structured, indent=2)
        elif result.content:
            output = str(result.content)
        else:
            output = "(no response from MCP)"

    provenance = f"\n[source: {metadata.source} | {metadata.duration_ms}ms]"
    if output and output != "(no response from MCP)":
        output = output + provenance

    if (
        cacheable
        and output
        and len(output) > 20
        and not output.startswith("[skill:")
        and not output.startswith("Error")
        and not output.startswith("MCP error")
        and output != "(no response from MCP)"
    ):
        mcp_manager.cache_result(server_id, tool_name, arguments, output)

    if image_blocks:
        return output, image_blocks
    return output


async def _execute_composed(
    skill: ResolvedSkill,
    *,
    input_text: str,
    model_provider: ModelProviderProtocol,
    model_name: str,
    mcp_manager: MCPClientManager | None = None,
) -> str:
    """Execute a composed skill via panel aggregation."""
    from ._panel import execute_panel  # noqa: PLC0415

    constituent_skills = list(skill.resolved_composes)
    if not constituent_skills:
        return f"[skill:{skill.id}] No constituent skills resolved."

    return await execute_panel(
        skill,
        constituent_skills,
        input_text=input_text,
        model_provider=model_provider,
        model_name=model_name,
    )
