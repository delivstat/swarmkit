---
title: Model provider tool-calling normalisation
description: Canonical tool-call format and per-provider translation. Every provider's tool protocol maps to one internal shape.
tags: [runtime, model-provider, tool-calling, m2.5]
status: approved
---

# Model provider tool-calling normalisation

## Goal

Every LLM provider has its own tool-calling protocol. SwarmKit skills
surface as tools to agents — the runtime must translate between the
canonical SwarmKit tool format and each provider's wire format. This
note defines:

1. The **canonical tool-call shape** (already sketched in
   `model-provider-abstraction.md` as `ToolSpec` + `ContentBlock`).
2. The **per-provider translation rules** — what adapters do.
3. **Edge cases** — parallel tool calls, streaming tool calls, tool
   errors, provider-specific quirks.

Blocks M5 (MCP integration) because MCP tool calls flow through this
normalisation layer. Does not block M3 (compiler) — M3 can use the mock
provider which returns canned tool-call responses.

## Non-goals

- **Designing the skill → tool mapping.** How a SwarmKit skill definition
  becomes a `ToolSpec` is the compiler's job (M3). This note only covers
  the provider-level translation.
- **Multi-turn tool-use orchestration.** The agentic loop (call model →
  get tool_use → execute tool → send tool_result → repeat) is the
  compiler's responsibility. This note covers the message format, not
  the loop.
- **MCP protocol details.** MCP has its own schema for tool definitions
  and results. The MCP integration (M5) maps MCP tools ↔ `ToolSpec`;
  this note covers `ToolSpec` ↔ provider wire format.

## Canonical types (already shipped)

From `model_providers/_types.py`:

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]    # JSON Schema for the tool's input

@dataclass(frozen=True)
class ContentBlock:
    type: Literal["text", "tool_use", "tool_result", "image"]
    text: str | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_result: Any = None
```

The `CompletionRequest.tools` field carries `Sequence[ToolSpec]`. The
model responds with `ContentBlock(type="tool_use", ...)`. The runtime
executes the tool and sends back `ContentBlock(type="tool_result", ...)`.

## Per-provider translation

### Anthropic

Closest to canonical — SwarmKit's format is modelled after Anthropic's.

| Canonical | Anthropic wire |
|---|---|
| `ToolSpec(name, description, input_schema)` | `{"name", "description", "input_schema"}` — identical |
| `ContentBlock(type="tool_use", tool_use_id, tool_name, tool_input)` | `{"type": "tool_use", "id", "name", "input"}` |
| `ContentBlock(type="tool_result", tool_use_id, tool_result)` | `{"type": "tool_result", "tool_use_id", "content"}` |
| `stop_reason="tool_use"` | `stop_reason="tool_use"` — identical |

Translation: rename `tool_use_id` ↔ `id`, `tool_name` ↔ `name`,
`tool_input` ↔ `input`. Trivial.

### OpenAI

OpenAI uses "function calling" with a different message structure.

| Canonical | OpenAI wire |
|---|---|
| `ToolSpec(name, description, input_schema)` | `{"type": "function", "function": {"name", "description", "parameters"}}` |
| `ContentBlock(type="tool_use", ...)` | Assistant message with `tool_calls[].function.{name, arguments}` (arguments is a JSON string, not a dict) |
| `ContentBlock(type="tool_result", ...)` | Separate message: `{"role": "tool", "tool_call_id", "content"}` |
| `stop_reason="tool_use"` | `finish_reason="tool_calls"` |

Key differences:
- **`arguments` is a JSON string**, not a parsed dict. Adapter must
  `json.dumps(tool_input)` outbound and `json.loads(arguments)` inbound.
- **Tool results are separate messages** with `role: tool`, not content
  blocks within the user message. Adapter must restructure the message
  list.
- **`tool_calls` is a list** — OpenAI supports parallel tool calls in a
  single response. Each gets its own `id`. Adapter maps each to a
  separate `ContentBlock`.
- **`parameters`** not `input_schema` in the tool definition.

### Google (Gemini)

Google uses `FunctionDeclaration` / `FunctionCall` / `FunctionResponse`.

| Canonical | Google wire |
|---|---|
| `ToolSpec(name, description, input_schema)` | `Tool(function_declarations=[FunctionDeclaration(name, description, parameters)])` |
| `ContentBlock(type="tool_use", ...)` | `Part(function_call=FunctionCall(name, args))` — args is a dict |
| `ContentBlock(type="tool_result", ...)` | `Part(function_response=FunctionResponse(name, response))` |
| `stop_reason="tool_use"` | `finish_reason=STOP` with `function_call` parts present |

Key differences:
- **Tools are wrapped** in a `Tool` object containing a list of
  `FunctionDeclaration`s. One `Tool` per request, not per-function.
- **No `tool_use_id`** — Google's protocol doesn't use correlation IDs.
  The adapter must match tool results to calls by `name` (which is
  ambiguous if the same tool is called twice). For v1.0, we enforce
  one-call-at-a-time for Google; parallel tool calls are Anthropic/OpenAI
  only.
- **`args` is a dict**, not a JSON string (unlike OpenAI). Consistent
  with canonical format.
- **`FunctionResponse.response`** must be a dict, not a bare string.
  Adapter wraps string results in `{"result": value}`.

### Ollama

Ollama's `/api/chat` endpoint supports tool calling for models that
have it (e.g. llama3.1, mistral). Format follows OpenAI's convention.

| Canonical | Ollama wire |
|---|---|
| `ToolSpec(name, description, input_schema)` | `{"type": "function", "function": {"name", "description", "parameters"}}` — same as OpenAI |
| `ContentBlock(type="tool_use", ...)` | `message.tool_calls[].function.{name, arguments}` |
| `ContentBlock(type="tool_result", ...)` | `{"role": "tool", "content": "..."}` |

Key difference: not all Ollama models support tools. Adapter should
check the model's capabilities or fail gracefully if the model returns
text instead of a tool call.

## Parallel tool calls

Anthropic and OpenAI support returning multiple `tool_use` blocks in a
single response. The canonical format handles this naturally — multiple
`ContentBlock(type="tool_use", ...)` in one `CompletionResponse.content`.

The runtime (M3 compiler) decides execution strategy:
- **Sequential:** execute each tool call in order, send results, re-prompt.
- **Parallel:** execute all tool calls concurrently, send all results at once.

The provider layer doesn't decide this — it just faithfully translates
multiple blocks. The compiler controls parallelism.

Google and Ollama: sequential only for v1.0 (no correlation IDs in
Google; model support varies in Ollama).

## Streaming tool calls

Tool calls during streaming are partially supported:

- **Anthropic:** streams `content_block_start` + `content_block_delta`
  events for tool_use blocks. The adapter accumulates deltas and yields
  a complete `ContentBlock` when the block closes.
- **OpenAI:** streams `tool_calls[].function.arguments` as incremental
  JSON chunks. Adapter accumulates until `finish_reason=tool_calls`.
- **Google:** streaming tool calls not documented as of v1.0 of
  google-genai. Treat as non-streaming — accumulate full response.
- **Ollama:** follows OpenAI streaming convention for tool calls.

For v1.0, the `stream()` method on each adapter yields `text` blocks
during streaming and falls back to `complete()` semantics when tool
calls are detected. Full streaming tool-call support is a v1.1 polish.

## `ToolSpec` ↔ `CompletionRequest` wiring

The `CompletionRequest.tools` field is already typed as
`Sequence[ToolSpec] | None`. Each adapter translates to its wire format
in `_to_<provider>_kwargs()`:

```python
# Anthropic — pass through (format matches)
if request.tools:
    kwargs["tools"] = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in request.tools
    ]

# OpenAI — wrap in function type
if request.tools:
    kwargs["tools"] = [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.input_schema}}
        for t in request.tools
    ]

# Google — wrap in Tool + FunctionDeclaration
if request.tools:
    kwargs["tools"] = [gtypes.Tool(function_declarations=[
        gtypes.FunctionDeclaration(name=t.name, description=t.description, parameters=t.input_schema)
        for t in request.tools
    ])]
```

## Implementation plan

This is a **design-only PR**. Implementation lands across two PRs:

1. **Tool-spec translation in each adapter** — add the `tools` field
   handling to `_to_<provider>_kwargs()` and parse `tool_use` blocks
   from responses. Unit-testable with recorded responses (no live API
   calls needed). Lands before M5.
2. **Streaming tool-call accumulation** — the `stream()` methods handle
   partial tool-call blocks. Lower priority; lands as polish before v1.0.

## Test plan (for the implementation PRs)

- **Per-adapter round-trip:** construct a `CompletionRequest` with tools,
  assert the translated wire format matches the provider's expected
  shape. Reverse: construct a raw provider response containing tool
  calls, assert the parsed `CompletionResponse` has correct
  `ContentBlock(type="tool_use", ...)` blocks.
- **Parallel tool calls:** Anthropic + OpenAI adapters handle multiple
  tool_use blocks in one response.
- **JSON string ↔ dict:** OpenAI's `arguments` string is correctly
  parsed to `tool_input` dict on inbound, and dumped on outbound.
- **Google missing correlation ID:** adapter assigns synthetic IDs for
  internal tracking.
- **Recorded-response fixtures** under
  `tests/fixtures/model-responses/` — one per provider per scenario.
  No network in unit tests.
