# The system prompt half of structured output

**Status:** implemented
**Scope:** `langgraph_compiler/_prompts.py`, `model_providers/_registry.py`

## How these were found

A Minder router topology scored **62.2%** on intent through SwarmKit against **65.4%** calling the
same model, with the same system prompt, schema and context, directly. Same everything, three points
apart — so the difference had to be in what SwarmKit actually sent. Capturing the payload at
`OllamaModelProvider.complete` showed three things.

## 1. Another agent's schema instructions, in every prompt

Every structured-output prompt ended with a hardcoded tail:

> *Every finding must have a 'fact' and 'source' field. If you found nothing relevant, return
> `{"findings": [], "not_found": ["<what you searched for>"]}`.*

Minder's router emits `{kind, subject, cameras, …}`. It has no `findings`, no `fact`, no `source`.
The wording belonged to one research topology and was appended to **every** agent with an
`output_schema`, telling each of them to produce a shape its own schema forbids.

Removed. Nothing schema-specific belongs in a function that runs for all schemas.

## 2. The schema pasted in, next to the grammar enforcing it — tried, measured, REVERTED

The router's system prompt arrived at **5112 characters against the topology's 2538**. The compiler
pastes the full JSON schema into the prompt *and* passes it as the provider's constraint —
`format` for Ollama, `response_format` for OpenAI, `response_schema` for Gemini — so the model is
told the same thing twice, and the agent's own instructions end up in the second half of their own
prompt.

Now conditional on whether the provider constrains decoding:

| provider | constrains? | prompt |
| --- | --- | --- |
| Ollama, OpenAI, Google | yes | schema omitted |
| Anthropic | **no** — it never reads `response_format` | schema in full |
| anything unknown | assumed no | schema in full |

The tool-bearing turn keeps a short line saying the format applies to the final answer, because a
grammar cannot express *when*. Everything else the grammar already says.

**This one was wrong, and the measurement says so.** Shipped in 1.195.0 and reverted immediately
after, on the same router topology that found the defects:

| | raw | guarded | `query` raw | p50 |
| --- | --- | --- | --- | --- |
| schema pasted | 62.2% | **89.7%** | 55% | 2.2s |
| paste skipped | **66.0%** | 84.6% | **29%** | **1.64s** |

Skipping it shortened prompts and cut latency 27%, and it dropped the `query` intent from 55% to 29%.

The premise was that constrained decoding makes the description redundant. It does not, because a
JSON Schema carries two things and the grammar carries one: **GBNF constrains shape and drops
`description`**. Those descriptions — *"For query/snapshot: person, vehicle, animal, or open for an
open-ended scene question"* — are the only thing telling a small model which intent it is looking at.
Guaranteeing the shape of an answer does not help a model that no longer knows which answer to give.

So the compiler always describes the schema. `provider_enforces_response_schema` and the
`grammar_enforced` parameter remain, unused by the compiler, for a caller whose schema has no
descriptions worth carrying — and as the record of why the obvious optimisation is not one.

It also corrects the framing this investigation started from. The 3.2-point "SwarmKit path gap" was
the pasted schema, confirmed in both directions — but the direct benchmark was not a *better*
configuration, only one that happened to omit the descriptions too. On the number that matters,
SwarmKit's configuration was **5 points ahead** the whole time.

## 3. `max_tokens` reached nobody

`_build_completion_request` read `temperature` from the agent's model config and not `max_tokens`,
which is a documented field on both the topology and archetype schemas (`{"type": "integer",
"minimum": 1}`) and validated on load. Authors could cap a node and get no cap.

One line. The same shape as the other two: declared, accepted, and read by nothing.

## Test plan

`packages/runtime/tests/test_structured_output_prompt.py` — no foreign schema words in any
instruction; enforced schemas omitted; enforced-with-tools keeps the *when* and drops the body;
unenforced schemas still described in full; the per-provider answers including the conservative
default; and `max_tokens` reaching the request.
