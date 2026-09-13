# Model provider

A **model provider** is a first-class SwarmKit artifact (`kind: ModelProvider`) that points the runtime at an LLM endpoint. It is **data**: a base URL, an auth shape, a model catalogue, capabilities and request quirks — layered over a wire-format **family** that stays code. A new OpenAI- or Ollama-compatible endpoint is a YAML file and nothing else; no Python, no runtime release.

The decision — what is data and what is code — is in the [declarative model providers design note](https://github.com/delivstat/swarmkit/blob/main/design/details/declarative-model-providers.md). This page is the artifact reference.

## The four families

There are four wire formats. They are Python, and they are the only Python:

| family | speaks | what extends it |
|---|---|---|
| `openai-compatible` | OpenAI chat-completions | OpenAI, OpenRouter, Groq, Together, llama.cpp `llama-server`, OpenVINO Model Server, vLLM, mlx-lm, Lemonade — every OpenAI-compatible server |
| `ollama` | Ollama's native `/api/chat` | Ollama, rkllama (Rockchip NPU) |
| `anthropic` | Anthropic Messages | Anthropic |
| `google` | Google GenAI | Google (API-key mode) |

A provider YAML names one via `extends` and parameterises it. Anything a family cannot express — OAuth token acquisition, a fifth wire format — is a new family, added as code, exactly as an executor past the declarative ceiling becomes a Tier-1 Python executor.

## The artifact

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

Same envelope as every other artifact — `apiVersion`, `kind`, `metadata`, `spec`, `provenance` — so the validator, the portal and the codegen treat it like one.

## `spec` fields

Required: `extends`. `spec` refuses unknown fields — the DSL is deliberately small and a template language cannot grow in by accident.

| Field (`spec.`) | Type | What it does |
|---|---|---|
| `extends` | id | **Required.** A family, or another provider's id. Resolved at load: the chain must end at a family and may not revisit an id. `extends: ollama` always means the *family*, never the bundled `ollama.yaml` — families and providers share a namespace and the family wins. |
| `base_url` | string | Endpoint. `${VAR:-default}` substitution applies, the same resolver as workspace env config. |
| `auth.api_key_env` | string | Env var holding the key. **Absent means no auth** — a local runtime, which registers unconditionally. |
| `auth.header` | string | Default `Authorization`. Azure's key mode is `api-key`. `openai-compatible` and `ollama` only. |
| `auth.scheme` | string | Default `Bearer`; empty string for a bare key. `openai-compatible` and `ollama` only. |
| `models.pattern` | regex | What `supports()` accepts. Default is the family's own catalogue (`^gpt-…`, `^claude-`, `^gemini-`). |
| `models.accept_any` | bool | Aggregators and local runtimes: the catalogue is unbounded and the server validates. Ollama's default. |
| `capabilities.*` | bool | `images`, `tools`, `streaming`, `structured_output`. **Narrow-only** — a `true` the family does not offer is a load-time error. A narrowed capability is enforced: a request carrying tools to a `tools: false` provider is refused before the wire. `structured_output: false` withholds `response_format` and keeps the schema in the prompt. |
| `options.lift_to_root` | list | Option keys moved from `options` to the payload root. Ollama's default is `[think, keep_alive]`. `ollama` only. |
| `headers` | map | Static extra request headers (OpenRouter's `HTTP-Referer`, `X-Title`). |
| `extra_body` | map | Fields merged into every request body that the base API does not define — OpenRouter's `usage: {include: true}` for per-call cost. `openai-compatible` and `ollama` only. |
| `requires` | `code` | Declares the provider cannot be expressed here. Load **refuses** it with the file and reason, so a YAML past the ceiling fails loudly rather than half-works. |

A field the family would ignore (`extra_body` on `anthropic`, say) is refused at load, not dropped: a declared quirk that never reaches the wire is exactly the half-working provider this artifact exists to prevent.

## Inheritance

`extends` names a family or another provider. Resolution walks the chain to a family, merging each hop's fields over its parent's: `auth`, `models`, `capabilities`, `headers` and `extra_body` merge by key; `base_url` and `lift_to_root` replace. A child overriding `auth.header` keeps its parent's `api_key_env`.

Depth is unbounded; every bundled provider is one hop. A provider three hops from its family is one nobody can read — that is a review norm, not a schema limit.

## Where providers come from

```
bundled    packages/runtime/src/swarmkit_runtime/model_providers/providers/*.yaml
workspace  <workspace>/providers/*.yaml           overrides a bundled id
```

**Registration is by readiness, not by a list.** Every loaded provider registers if its `auth.api_key_env` is set, or if it declares no auth. Adding a provider is adding a file; there is nothing in Python to keep in step.

The bundled library: `anthropic`, `openai`, `google`, `ollama`, `openrouter`, `groq`, `together`, plus the edge runtimes `rkllama`, `llama-server`, `openvino-model-server`, `mlx-lm`, `lemonade`. Each local runtime's `base_url` reads an env var with a default (`RKLLAMA_HOST`, `LLAMA_SERVER_URL`, `OVMS_URL`, `MLX_LM_URL`, `LEMONADE_URL`; Ollama's is `OLLAMA_BASE_URL`).

## Inspecting

```
$ swarmkit providers list
  anthropic                anthropic          bundled    needs ANTHROPIC_API_KEY
  groq                     openai-compatible  bundled    ready (GROQ_API_KEY set)
  llama-server             openai-compatible  workspace  ready (no auth)
  rkllama                  ollama             bundled    ready (no auth)
  …

$ swarmkit providers show rkllama
id:           rkllama
chain:        rkllama -> ollama
base_url:     http://localhost:8080
auth:         none
models:       any
capabilities: images=yes, tools=no, streaming=yes, structured_output=yes
lift_to_root: think, keep_alive
```

A workspace YAML declaring `requires: code` stops `list` with its file name and the reason.

## Adding one

Point a workspace at an OpenAI-compatible server SwarmKit has never heard of:

```bash
mkdir -p workspace/providers
cat > workspace/providers/vllm.yaml <<'YAML'
apiVersion: swarmkit/v1
kind: ModelProvider
metadata: { id: vllm, name: vLLM, description: vLLM's OpenAI-compatible server on the GPU box. }
spec:
  extends: openai-compatible
  base_url: ${VLLM_URL:-http://gpu-box:8000/v1}
  models: { accept_any: true }
provenance: { authored_by: human, version: 1.0.0 }
YAML
swarmkit providers list workspace       # vllm  openai-compatible  workspace  ready (no auth)
```

Then `provider: vllm` in a topology or archetype, or `SWARMKIT_PROVIDER=vllm` for a run.
