# Surface structured MCP tool output

**Scope:** runtime (`langgraph_compiler/_skill_executor.py`)
**Design reference:** §18 (MCP integration)
**Status:** implemented

## Goal

When an MCP tool returns structured output (`structuredContent` / output
schema) but no text content block, surface the structured data to the agent
instead of reading the result as empty.

## Non-goals

- Changing behaviour for tools that return a text block (the common FastMCP
  case). Text stays primary — no regression.
- Surfacing `structuredContent` *in addition to* text. FastMCP already
  serialises structured output into the text block, so doing both would
  duplicate the payload to the model.
- Parsing/validating structured output against the tool's output schema
  (separate future work).

## Problem

`_execute_mcp_tool` built the model-visible output purely from text content
blocks:

```python
output = "\n".join(text_parts) or str(result.content) if result.content else "(no response from MCP)"
```

The MCP spec (2025-06-18) allows a tool to return `structuredContent` and omit
the text fallback. Such a tool currently surfaces as `str(result.content)`
(e.g. `[TextResourceContents(...)]`) or `(no response from MCP)` — the
structured payload is dropped. FastMCP-based servers are unaffected because
FastMCP always includes the serialised payload in a text block, but
spec-compliant non-FastMCP servers can return structured-only results.

## Design

Text content remains primary. Only when no usable text is present do we fall
back to `structuredContent`, serialised as indented JSON:

```python
text_output = "\n".join(text_parts)
if text_output:
    output = text_output
else:
    structured = getattr(result, "structuredContent", None)
    if structured:
        output = json.dumps(structured, indent=2)
    elif result.content:
        output = str(result.content)
    else:
        output = "(no response from MCP)"
```

This avoids a subtle regression: FastMCP wraps a plain `-> str` return as
`structuredContent = {"result": "<string>"}`. Preferring structured content
unconditionally would leak that wrapper to the model. Keeping text primary
sidesteps it entirely.

## Test plan

`packages/runtime/tests/test_structured_tool_output.py`:
- structured content surfaced when no text block is present
- text remains primary when both are present (wrapper does not leak)
- empty result still yields the `(no response from MCP)` sentinel

## Demo

`uv run pytest packages/runtime/tests/test_structured_tool_output.py -q` → 3 passed.
Regression: `test_skill_executor.py`, `test_mcp_provenance.py`,
`test_tool_loop_guards.py`, `test_github_mcp_skills.py` → all pass.
