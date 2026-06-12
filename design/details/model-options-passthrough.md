# Generic per-model options passthrough

**Scope:** schema (`model` def), runtime (`CompletionRequest`, all four model
providers, the agent-turn request builder)
**Design reference:** §14.3 (LangGraph compiler), model-provider-abstraction.md
**Status:** implemented

## Goal

Let a topology/archetype `model:` block carry an `options` object of
provider-native runtime parameters, and have whichever provider runs the agent
fold those options into its native call. One generic mechanism, all providers —
not a per-provider special case.

```yaml
model:
  provider: ollama
  name: llama3.2:3b
  temperature: 0
  options:
    num_ctx: 8192       # Ollama default is ~2048 and silently truncates
    repeat_penalty: 1.15
```

```yaml
model:
  provider: openai
  name: gpt-4o
  options:
    top_p: 0.9
    frequency_penalty: 0.2
```

## Motivation

The immediate driver was Ollama's `num_ctx`. Ollama defaults to a small context
window (~2048 tokens) and **silently truncates** any longer prompt from the
front — exactly where the system prompt and tool-schema instructions live. A
SwarmKit agent prompt (persona + structured-output injection + every tool's JSON
schema + history) routinely exceeds 2048, so the model loses its instructions
mid-run and degenerates into repetition loops and off-task output. There was no
way to set `num_ctx` (or `repeat_penalty`, `top_k`, OpenAI `top_p`,
`frequency_penalty`, a `seed`, …) from the artifact — the compiler only read
`temperature`.

Rather than bolt an Ollama-specific `num_ctx` knob onto the provider, the fix is
a generic `options` passthrough usable by every provider.

## Non-goals

- Validating option *keys* against each provider's parameter set. Keys are
  passed through verbatim; an invalid key surfaces as a loud provider error
  (same as a bad OpenAI kwarg today), not a silent drop.
- A framework default for `num_ctx`. Larger contexts cost more KV-cache VRAM,
  which is exactly the constraint on the small-GPU deployments that need this.
  The safe default is "whatever the provider does"; operators opt in per model.
- Applying options to non-agent LLM calls (synthesizer, governance-retry,
  authoring chat). Those use their own model abstractions; this covers the
  agent's own turns, including every tool-loop iteration.

## Design

1. **Schema.** `options` added as an explicit property on the `model` def in
   `topology.schema.json` and `archetype.schema.json` — `type: object`,
   `additionalProperties: true`. The block already allowed extra keys
   (`additionalProperties: true`), so this is documentation + codegen typing,
   not a new validation rule; no invalid fixture applies.

2. **Canonical type.** `CompletionRequest` gains `options: dict[str, Any] |
   None`. Distinct from `extra` (runtime-internal passthrough such as
   `base_url` / `tool_choice`); `options` is the artifact-authored bucket.

3. **Compiler.** `_build_completion_request` reads `agent.model["options"]`
   into `CompletionRequest.options`. This is the single agent-turn construction
   point, so options apply to plain turns and tool-loop turns alike.

4. **Providers.** Each folds `request.options` into its native call, **after**
   the first-class fields so a same-named option (e.g. `temperature`) overrides
   them:
   - **Ollama** → merged into the native `options` object (`num_ctx`,
     `repeat_penalty`, `top_k`, …).
   - **OpenAI / OpenRouter / Groq / Together** → top-level call kwargs
     (`top_p`, `frequency_penalty`, `seed`, …), before `extra` so runtime
     `extra` stays authoritative.
   - **Anthropic** → top-level `messages.create` / `messages.stream` kwargs
     (`top_p`, `top_k`, `stop_sequences`, …).
   - **Google** → `GenerateContentConfig` fields (`top_p`, `top_k`, …).

## Precedence

`first-class fields` → `options` → (`extra`, where the provider applies it).
First-class `temperature`/`max_tokens` set the baseline; `options` layer over
them (so authors can override); runtime `extra` is applied last for the
providers that splat it, keeping functional runtime params authoritative.

## Test plan

- Schema: `with-model-options.yaml` topology fixture (Ollama + OpenAI option
  blocks) validates in both Python and TS suites; codegen emits the `options`
  field for both languages.
- Providers (`test_model_providers.py`): Ollama folds options into the options
  object and a same-named option overrides the first-class value; no options →
  no `options` key; OpenAI options become top-level kwargs; Google options
  become `GenerateContentConfig` fields.
- Compiler (`test_output_schema.py`): `model.options` flows to
  `CompletionRequest.options`; absent options → `None`.

## Demo

A worker on `llama3.2:3b` via Ollama with `options: {num_ctx: 8192}` keeps its
full system prompt and tool schemas in context instead of being truncated at
2048 — the looping/repetition failure mode disappears on tool-heavy agents.
Inspect the request Ollama receives: `options.num_ctx == 8192`.
