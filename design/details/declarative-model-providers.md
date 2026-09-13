# Declarative model providers — a provider is data, a wire format is code

**Status:** shipped — runtime 1.219.0, schema 1.41.0. Reference page: `docs/site/reference/model-provider.md`.
**Precedent:** [`executor-declarative-adapters-plan.md`](executor-declarative-adapters-plan.md) — *"a new harness is added as data, with no Python and no runtime release."* This is the same decision, one seam over.

## Goal

Add a model provider by dropping a YAML file into a workspace, or into the bundled library, with no Python and no runtime release. Let a provider inherit from an existing one and override only what differs.

## Why now

Three of the eight providers are already data pretending to be code:

```python
class OpenRouterModelProvider(OpenAIModelProvider):   # base_url + OPENROUTER_API_KEY
class GroqModelProvider(OpenAIModelProvider):         # base_url + GROQ_API_KEY
class TogetherModelProvider(OpenAIModelProvider):     # base_url + TOGETHER_API_KEY
```

Each is a subclass whose entire content is a URL and an env-var name. And the endpoint is not configurable at all — `register_available_providers` does `OllamaModelProvider()` with no arguments, so the only way to point SwarmKit at a different Ollama-API server today is to run it on port 11434 in place of Ollama.

Meanwhile the edge-inference world converged on two wire formats. Every accelerator runtime worth targeting exposes one:

| runtime | silicon | speaks |
| --- | --- | --- |
| rkllama, rkllm-server | Rockchip NPU | Ollama API / OpenAI-compatible |
| OpenVINO Model Server | Intel CPU · iGPU · NPU | OpenAI-compatible |
| llama.cpp `llama-server` | any CPU; Vulkan/OpenCL GPU incl. Mali | OpenAI-compatible |
| vLLM, TensorRT-LLM | NVIDIA / AMD | OpenAI-compatible |
| mlx-lm | Apple Silicon | OpenAI-compatible |
| Lemonade | AMD Ryzen AI NPU | OpenAI-compatible |

None of these needs a provider. All of them need a URL. Today each would be a Python subclass and a release; after this, each is six lines of YAML in a directory.

## Non-goals

- **A template language.** The DSL is deliberately minimal and says so. Anything past its ceiling declares `requires: code` and becomes a family (below), exactly as the executor DSL graduates to a Tier-1 Python executor.
- **OAuth, service accounts, token refresh.** Every provider that exists uses one auth shape — a static key in a header — and nothing has asked for another. Azure's *key* mode differs only in the header name, which the DSL covers. Entra ID tokens and Vertex AI service accounts are genuinely different (acquisition, refresh, expiry) and are the `requires: code` case if and when.
- **Widening capabilities.** A YAML may *narrow* what its family offers (`tools: false` for a runtime that half-supports them). It may never claim a capability the family does not implement — that is how a silent wrong answer reaches an audit log.
- **Changing invariant 4.** *Only `model_providers/` imports vendor SDKs.* The YAML parameterises a family; it never imports anything.

## The line: a wire format is code, everything else is data

What varies between providers, sorted by what it actually is:

| varies | data? |
| --- | --- |
| base URL | yes |
| auth — env var, header name, scheme | yes |
| model catalogue — what `supports()` accepts | yes |
| capabilities — images, tools, streaming, structured output | yes, **narrow-only** |
| request quirks — Ollama lifting `think`/`keep_alive` to the payload root; OpenRouter's `usage: {include: true}` | yes, as a declared list |
| extra headers — `HTTP-Referer`, `X-Title` for OpenRouter | yes |
| **wire format** — OpenAI chat-completions · Anthropic messages · Google · Ollama-native | **no — code** |

There are **four wire formats**. They stay Python, renamed **families**: `openai-compatible`, `anthropic`, `google`, `ollama`. A provider YAML names one via `extends` and parameterises it.

## The artifact: `kind: ModelProvider`

```yaml
apiVersion: swarmkit/v1
kind: ModelProvider
metadata:
  id: groq
  name: Groq
  description: OpenAI-compatible inference with very fast tokens.
spec:
  extends: openai-compatible
  base_url: https://api.groq.com/openai/v1
  auth:
    api_key_env: GROQ_API_KEY
provenance:
  authored_by: human
  version: 1.0.0
```

Same envelope as every other artifact — `apiVersion`, `kind`, `metadata`, `spec`, `provenance` — so the
validator, the portal and the codegen treat it like one.

That is the whole of `GroqModelProvider`. The rkllm case, which today needs a release:

```yaml
apiVersion: swarmkit/v1
kind: ModelProvider
metadata:
  id: rkllama
  name: rkllama — Rockchip NPU
  description: Ollama-API server for RK3588/RK3576 NPUs, running pre-converted .rkllm models.
spec:
  extends: ollama
  base_url: ${RKLLAMA_HOST:-http://localhost:8080}
  capabilities:
    tools: false          # narrows the family; may never widen it
provenance:
  authored_by: human
  version: 1.0.0
```

### `spec` fields

| field | type | notes |
| --- | --- | --- |
| `extends` | id | **required.** A family, or another provider. Resolved at load; one level of override-merge per hop; cycles refused by name |
| `base_url` | string | `${VAR:-default}` substitution, same resolver as everything else |
| `auth.api_key_env` | string | absent means no auth — a local runtime |
| `auth.header` | string | default `Authorization`; Azure's key mode is `api-key` |
| `auth.scheme` | string | default `Bearer`; empty string for a bare key |
| `models.pattern` | regex | what `supports()` accepts. Default from the family |
| `models.accept_any` | bool | aggregators: the catalogue is unbounded and the server validates |
| `capabilities.*` | bool | `images`, `tools`, `streaming`, `structured_output`. **Narrow-only** — a `true` the family does not offer is a load-time error |
| `options.lift_to_root` | list | option keys moved from `options` to the payload root — Ollama's `think`, `keep_alive` |
| `headers` | map | static extra headers |
| `requires` | `code` | declares the provider cannot be expressed here. Load refuses it with the reason, so a YAML past the ceiling fails loudly rather than half-works |

### Inheritance

`extends` names a family or another provider. Resolution walks the chain to a family, merging each hop's fields over its parent's. `auth`, `models`, `capabilities`, `headers` and `extra_body` merge by key; `base_url` and `lift_to_root` replace. A chain that does not end at a family, or that revisits an id, is refused at load with the chain printed.

**Families and providers share one namespace, and the family wins.** Four bundled providers carry their family's name — `anthropic` the provider extends `anthropic` the family. So the first hop is always the provider named, and every `extends` after it resolves to a family before a provider: `rkllama`'s `extends: ollama` is the wire format, never the bundled `ollama.yaml`. (Found by the bundled-library test: a walk that consulted the family table first returned the bare family for `anthropic`, its `auth.api_key_env` was never read, and nothing registered.)

**A field the family would ignore is refused, not dropped.** `extra_body` on `anthropic`, `lift_to_root` on `openai-compatible`, `auth.header` on `google` — each family owns its wire format, and a declared quirk that never reaches the wire is the half-working provider this note exists to prevent. `FAMILY_FIELDS` in `_declarative.py` is the table.

**A narrowed capability is enforced, not decorative.** `tools: false` refuses a request carrying tools before the wire, naming the provider and the field; `images: false` likewise; `streaming: false` refuses `stream()`; `structured_output: false` withholds `response_format` and flips `enforces_response_schema`, so the compiler pastes the schema into the prompt instead.

Depth is unbounded but every bundled provider is one hop. That is a review norm, not a schema limit: a provider three hops from its family is one nobody can read.

## Loading and registration

Mirrors `executors/_declarative.py` exactly:

```
bundled    packages/runtime/src/swarmkit_runtime/model_providers/providers/*.yaml
workspace  <workspace>/providers/*.yaml           overrides a bundled id
```

**Registration is by readiness, not by a hardcoded list.** `register_available_providers` today keeps a list of `(ENV_VAR, ClassName)` pairs. It becomes: every loaded provider registers if its `auth.api_key_env` is set, or if it has no auth. The list disappears. A provider without auth — every local runtime — registers unconditionally, which is the current Ollama behaviour, made general.

`swarmkit providers list` shows every provider, its family, its source and whether it is ready — the one question an operator has when `provider: groq` is not registering. `swarmkit providers show <id>` prints the resolved chain.

## Migration

`openrouter`, `groq` and `together` are rewritten as bundled YAML and their Python subclasses deleted. **Behaviour-identical, asserted**: a test constructs each from YAML and from the deleted class and compares the resulting client configuration. `anthropic`, `openai`, `google` and `ollama` gain a YAML each too, so every provider is described one way — but their families stay as the code they parameterise.

The provider ids do not change. A workspace saying `provider: groq` works before and after.

## What changes, file by file

**New**
- `packages/schema/schemas/model-provider.schema.json` — follows `docs/notes/schema-change-discipline.md`; fixtures under `packages/schema/tests/fixtures/model-provider/`
- `model_providers/_declarative.py` — `ProviderSpec`, `parse_provider_spec`, `resolve_chain`, `load_provider_specs(workspace_root)`, `build_provider(spec)`
- `model_providers/_family.py` — `FamilyBase`: the constructor parameters every family accepts, `supports()` from the catalogue, and the capability checks
- `model_providers/providers/*.yaml` — the bundled library: the existing eight, plus `rkllama`, `openvino-model-server`, `llama-server`, `mlx-lm`, `lemonade`
- `tests/test_declarative_providers.py`

**Changed**
- `_openai.py`, `_ollama.py`, `_anthropic.py`, `_google.py` — each becomes a family: accepts `base_url`, `auth`, `headers`, `lift_to_root`, `capabilities`, `models` as constructor parameters. No behaviour change when called with none of them.
- `_openai_compat.py` — deleted; its three classes are YAML now
- `_workspace_runtime.register_available_providers` — loads the library and registers by readiness; takes the workspace root so workspace providers load
- `_registry.provider_enforces_response_schema` — answers through the YAML (family, narrowed) rather than a class scan
- `cli/_cmd_providers.py` — `swarmkit providers list|show`
- `docs/`, `llms.txt`, `llms-full.txt` — the provider list and how to add one

**Unchanged, deliberately**
- `ModelProviderProtocol` — `complete()` and `supports()`. A YAML-built provider satisfies it the same way.
- Invariant 4.

## Test plan

- **Parse and refuse.** Every field; a missing `extends`; a chain that never reaches a family; a cycle; `capabilities.tools: true` on a family without tools; `requires: code`.
- **Inheritance merges by key.** A child overriding `auth.header` keeps the parent's `api_key_env`.
- **Migration is behaviour-identical.** `groq`, `openrouter`, `together` from YAML produce the same client `base_url`, key source and extra headers as the deleted classes.
- **Registration by readiness.** With `GROQ_API_KEY` unset, `groq` is absent; set, present. `ollama` and `rkllama` present regardless.
- **Workspace overrides bundled.** A workspace `providers/ollama.yaml` with a different `base_url` wins.
- **`${VAR:-default}` resolves** in `base_url`.
- **The bundled library is valid** against the schema, and every bundled id is unique.

## Demo plan

Point a workspace at an OpenAI-compatible server it has never heard of, with no Python:

```bash
cat > workspace/providers/llama-server.yaml <<EOF
apiVersion: swarmkit/v1
kind: ModelProvider
metadata: { id: llama-server, name: llama.cpp, description: llama-server, OpenAI-compatible. }
spec:
  extends: openai-compatible
  base_url: \${LLAMA_SERVER_URL:-http://localhost:8081/v1}
provenance: { authored_by: human, version: 1.0.0 }
EOF
swarmkit run --workspace workspace --provider llama-server --model any "hello"
```

And the reverse — the ceiling refusing loudly:

```
$ swarmkit providers list
error: providers/vertex.yaml declares requires: code — this provider cannot be expressed
       declaratively (OAuth token acquisition). Implement it as a family.
```

## Acceptance

- A new OpenAI- or Ollama-compatible endpoint is a YAML file and nothing else.
- The three aggregator subclasses are gone and nothing observable changed.
- The endpoint is configurable for every provider.
- A YAML cannot claim a capability its family lacks.
