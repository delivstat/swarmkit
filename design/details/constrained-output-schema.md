# Schema-constrained output decoding

**Scope:** runtime (`langgraph_compiler/_prompts.py`, `model_providers/_ollama.py`)
**Design reference:** §14.3 (LangGraph compiler), model-provider-abstraction.md
**Status:** implemented

## Goal

When a worker has an effective `output_schema`, drive **true schema-constrained
decoding** at every provider that supports it — not just "produce some JSON".
The model's grammar/logit masking is constrained to the schema shape, so the
output is on-schema by construction rather than by hope.

## Non-goals

- Strict mode by default. We do not set OpenAI `strict: true` (which requires
  `additionalProperties: false` and every property in `required`). Many
  `output_schema`s are intentionally loose; forcing strict would reject them.
  Strict can be opted into per-schema later.
- Changing which agents get a schema. `get_effective_output_schema` already
  decides that (workers get the default findings schema; leaders/root do not
  unless explicit). This change only affects *how* the schema is transmitted.
- Anthropic. The Anthropic Messages API has no response-schema parameter; the
  schema continues to reach Claude via the system-prompt injection that
  `_build_system_prompt` already performs.

## Problem

`_build_completion_request` collapsed the effective schema into
`{"type": "json_object"}` before handing it to the provider. The actual schema
was discarded. Downstream:

- **Ollama** mapped both `json_object` and `json_schema` to `format="json"` —
  plain JSON mode, no schema constraint.
- **Google** already read `response_format["json_schema"]["schema"]` into
  `response_schema`, but never received it because the compiler never sent a
  `json_schema`.
- **OpenAI / OpenRouter** passed `response_format` through unchanged, so they
  only ever got `json_object` (JSON mode), never native structured output.

Capable hosted models (e.g. Kimi via OpenRouter) produce on-schema JSON even in
plain JSON mode, which masked the gap. Small local models (llama3.2:3b via
Ollama) do not — they emit malformed or off-schema output, so the gap surfaced
only once a worker ran on a 3B local model.

## Change

1. `_build_completion_request` now emits the real schema:

   ```python
   response_format = {
       "type": "json_schema",
       "json_schema": {"name": "agent_output", "schema": effective_schema},
   }
   ```

2. `_to_ollama_payload` translates `json_schema` to Ollama structured outputs by
   setting `format` to the schema object itself (falling back to `"json"` if a
   schema is somehow absent). `json_object` still maps to `format="json"`.

Google and OpenAI/OpenRouter need no provider change — they already consume the
`json_schema` shape correctly once the compiler sends it.

## Provider matrix

| Provider | Control set from `json_schema` | Change needed |
| --- | --- | --- |
| Ollama | `format = <schema>` | `_ollama.py` (this PR) |
| Google | `response_schema = <schema>` + `response_mime_type` | none (already correct) |
| OpenAI / OpenRouter / Groq / Together | `response_format` passthrough | none (already correct) |
| Anthropic | schema via system-prompt injection | none (no API parameter) |

## Test plan

- `test_output_schema.py`: `_build_completion_request` carries the real schema
  under `json_schema.schema` for both the default worker schema and an explicit
  custom schema; leaders / opted-out workers still get `None`.
- `test_model_providers.py`: each provider's payload builder translates a
  `json_schema` response_format into its native control —
  - Ollama → `format` equals the schema object; `json_object` stays `"json"`;
    no response_format omits `format`.
  - OpenAI → `response_format` passed through verbatim.
  - Google → `response_schema` set to the schema, `response_mime_type` JSON;
    `json_object` sets no `response_schema`.

## Demo

Run any worker on a local Ollama model with an `output_schema` and inspect the
request Ollama receives — `format` is the JSON schema, so decoding is grammar
constrained. Before this change the same worker sent `format="json"` and a 3B
model frequently returned off-schema text.
