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

## 2. The schema pasted in, next to the grammar enforcing it

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

**The default is "does not enforce"** and unknown providers get the prose. A provider that needs the
schema in its prompt and does not get it produces garbage; a provider that gets it redundantly only
wastes tokens. The failure modes are not symmetric, so the default follows the worse one.

`provider_enforces_response_schema` is deliberately **not** on `ModelProviderProtocol`: making it
mandatory would break third-party providers, and every existing test double, over a capability most
have no opinion about.

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
