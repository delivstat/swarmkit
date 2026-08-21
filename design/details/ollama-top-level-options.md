# Ollama's top-level payload keys

**Status:** implemented
**Scope:** `packages/runtime/src/swarmkit_runtime/model_providers/_ollama.py`

## Goal

Make `think` settable per agent — from an archetype or a topology's `model.options` — instead of
being decided by a hardcoded model-family match.

## The defect

`model.options` is provider-native passthrough, and `NON_NATIVE_OPTIONS` already lists `think` and
`keep_alive` so non-Ollama adapters drop them. Ollama, being their native home, folds the whole
options dict:

```python
options.update(request.options)
payload["options"] = options
```

But **`think` and `keep_alive` are top-level fields of Ollama's `/api/chat` payload, not members of
its `options` object.** Authoring `model.options.think: false` today produces
`payload["options"]["think"] = false`, which Ollama silently ignores. The option is accepted by the
schema, carried by the runtime, dropped correctly by every other provider — and does nothing at the
one provider it exists for. Declared, validated, passed along, honoured by nobody.

## Why it matters now

Reasoning is charged against the same budget as the answer. Measured on Minder's router topology,
which sets `max_tokens: 256`:

```
qwen3.5:0.8b, num_predict=256
  done_reason: length   eval_count: 256
  thinking: 1076 chars  content: ''      <- empty
```

All 256 tokens went inside the thinking block; the reply is empty. A model configured this way looks
*incapable* rather than *misconfigured*, which is the expensive kind of wrong. `think: false` fixes
it — 48 tokens, clean stop.

The existing `_THINKING_MODEL_FAMILIES = ("gemma",)` match cannot express this: Qwen3, Qwen3.5 and
DeepSeek-R1 all reason, the list only names Gemma, and extending a hardcoded tuple every time a
family ships reasoning is the pattern this replaces. Per-agent configuration is also the right
granularity — the same model may want reasoning for a planner and not for a router.

## Design

Hoist the two keys out of `options` into the payload root after folding:

```python
for key in _OLLAMA_TOP_LEVEL_OPTIONS:      # ("think", "keep_alive")
    if key in options:
        payload[key] = options.pop(key)
```

The Gemma default stays, but explicit configuration wins — an author who writes `think: true` for a
Gemma agent gets it. That ordering matters: the default exists because Gemma's thinking breaks
Ollama's tool-call parser, so it is a good default and a bad law.

Non-goals: no schema change (`model.options` is already free-form passthrough), no change to any
other provider (`NON_NATIVE_OPTIONS` already drops both keys), and no attempt to auto-detect
reasoning families — naming the model family is what this replaces.

## Test plan

* `think`/`keep_alive` in `model.options` land at payload root, not inside `options`
* neither key is left behind in `options`
* explicit `think` overrides the Gemma default, in both directions
* the Gemma default still applies when nothing is configured
* unrelated options (`num_ctx`, `repeat_penalty`) still fold into `options`
* non-Ollama adapters still drop both keys (existing `test_openai_drops_ollama_only_options`)

## Demo

`just demo-ollama-think` — builds the payload both ways and prints it, showing the key at the root
where Ollama reads it rather than nested where it does not.
