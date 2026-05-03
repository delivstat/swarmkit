---
title: Model provider abstraction
description: ModelProvider ABC, built-in providers (anthropic, openai, google, ollama, mock), plugin path. Mirrors GovernanceProvider (§8.5). Blocks M3.
tags: [runtime, model-provider, abstraction, m2.5]
status: approved
---

# Model provider abstraction

**Scope:** `packages/runtime/src/swarmkit_runtime/model_providers/`
**Design reference:** §10.2 (agent `model` block), §13 (archetype defaults), §14.3 (LangGraph compiler dispatches to models), mirrors §8.5 (`GovernanceProvider`).
**Status:** proposed — blocks **M3**.

## Goal

Give users a single way to declare which LLM a given agent uses — Anthropic, OpenAI, Google, local Ollama, or anything custom — without the runtime hard-coding any SDK. Topology YAML stays provider-agnostic; the runtime resolves via registered providers at load time; developers add new providers through a plugin interface without touching the framework.

This is the parallel of `GovernanceProvider` (§8.5): narrow interface, several built-in implementations, stable boundary.

## Non-goals

- Model **routing** or automatic failover. The topology declares which model; the runtime uses it. Any routing logic is a separate concern (future skill category, not the provider layer).
- Abstracting the **execution engine** (LangGraph) — out of scope here; see §7 principle "Framework-aligned, not framework-locked."
- A new message format. The internal canonical message structure follows Anthropic's messages shape (roles, content blocks, tool_use / tool_result) because it is the most expressive of the three major providers; provider adapters translate to/from it. Format choice is revisitable but should not be a design question per call.
- Tool-calling semantics in depth — covered by a follow-up note (`model-provider-tool-calling.md`) because each provider has its own tool-call protocol and a clean normalisation deserves its own PR. This note states the interface shape; the tool-calling details land before M3.

## Why this deserves its own abstraction

Three concrete forces push in the same direction:

1. **User choice.** From the user's message on PR #5 review: agents should be configurable per-leader / per-worker / per-archetype. An engineering team running Opus on leaders and local Ollama on workers should not need a fork of the framework.
2. **Vendor neutrality.** SwarmKit is positioned as the "Terraform for swarms." Committing the runtime to any one SDK fails that positioning. Anthropic is our own primary LLM, but the framework makes no assumption beyond "an LLM the user picked."
3. **Local inference.** Ollama and similar local runtimes matter for privacy-sensitive and cost-sensitive users. First-class support for local is a differentiator vs. frameworks that assume hosted APIs.

## API shape

```python
# packages/runtime/src/swarmkit_runtime/model_providers/__init__.py

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | Sequence[ContentBlock]  # string for simple text, blocks for tool_use / tool_result

@dataclass(frozen=True)
class ContentBlock:
    type: Literal["text", "tool_use", "tool_result", "image"]
    # Canonical fields; provider adapters read what they need.
    text: str | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_result: Any | None = None

@dataclass(frozen=True)
class CompletionRequest:
    model: str                          # provider-specific model name, e.g. "claude-sonnet-4-6"
    messages: Sequence[Message]
    system: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    tools: Sequence[ToolSpec] | None = None     # canonical SwarmKit tool spec; adapter translates
    extra: dict[str, Any] | None = None         # provider-specific passthrough

@dataclass(frozen=True)
class CompletionResponse:
    content: Sequence[ContentBlock]
    stop_reason: Literal["end_turn", "max_tokens", "tool_use", "error"]
    usage: Usage                        # input/output/cache tokens — see governance overhead budget (§8.6)
    raw: Any = None                     # provider response for debugging; never relied on in runtime logic


class ModelProvider(ABC):
    """Narrow abstraction over LLM providers. Stable across framework versions.

    Implementations must be importable without their backing SDK being
    installed if `supports(...)` can return False for every model. That lets
    users install SwarmKit + only the SDKs they use.
    """

    provider_id: ClassVar[str]          # "anthropic", "openai", "google", "ollama", custom slugs

    @abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """One-shot completion. Tools, if provided, are translated to provider format."""

    @abstractmethod
    def stream(self, request: CompletionRequest) -> AsyncIterator[ContentBlock]:
        """Token / content-block streaming. Yields partial blocks until stop_reason terminates."""

    @abstractmethod
    def supports(self, model: str) -> bool:
        """Whether this provider can serve the given model name. Fast, no network call."""

    def tokenize(self, text: str, model: str) -> int | None:
        """Optional token count for budgeting; None if the provider can't compute it cheaply."""
        return None
```

## Built-in providers (v1.0)

Ship in `packages/runtime/src/swarmkit_runtime/model_providers/builtin/`:

| provider_id | SDK | Notes |
|---|---|---|
| `anthropic` | `anthropic>=0.39` | First-class. Default model `claude-sonnet-4-6`. |
| `openai` | `openai>=1.50` | Also handles `azure-openai` variant via `extra.base_url` and Azure auth; no separate provider_id. |
| `google` | `google-genai>=1.0` | Gemini family. |
| `ollama` | `httpx` only (no SDK) | Local inference. Defaults to `http://localhost:11434`; `extra.base_url` overrides. |
| `mock` | none | Deterministic responses keyed by request hash; test-only. Always importable. |

Each SDK is a soft dependency: the provider module declares `provider_id` and attempts its import lazily. A user topology that names `provider: anthropic` fails at load time with a clean "anthropic SDK not installed; `uv tool install swarmkit-runtime[anthropic]` or `pip install swarmkit-runtime[anthropic]`" message.

Ranking rationale (why these five):
- **Anthropic** — SwarmKit's primary LLM partner; authoring swarms target Claude.
- **OpenAI** — largest market share; users must have an easy path.
- **Google** — rapidly closing the gap, notably cost-competitive for long context.
- **Ollama** — the open-weights story. Covers most local setups including llama.cpp-compatible runtimes via its OpenAI-compatible endpoint.
- **Mock** — non-optional; tests everywhere depend on it.

## Registration & plugin mechanism

Two layers:

1. **Built-ins** — auto-registered when `swarmkit_runtime.model_providers` is imported. No user config needed.
2. **Custom providers** — discovered via Python entry points group `swarmkit.model_providers`. A third-party package declares:

   ```toml
   [project.entry-points."swarmkit.model_providers"]
   acme-cloud = "acme_swarmkit_plugin:AcmeModelProvider"
   ```

   At runtime, `pkg_resources` / `importlib.metadata` enumerates entries in the group and registers each class.

3. **Per-workspace override** — `workspace.yaml` may declare providers by fully-qualified class path:

   ```yaml
   model_providers:
     - class: my_internal_pkg.InternalProvider
       config:
         base_url: https://internal-llm.example.com
   ```

   This path does not require the provider to be published as an installable package — useful for org-internal providers.

Resolution order when a topology references `provider: foo`: workspace overrides → entry-point plugins → built-ins. First match wins. Duplicate IDs fail topology load.

## Credentials & config

Credentials are **never** in topology YAML. Topologies are shareable artifacts; credentials are deployment-specific. Per-provider conventions:

| Provider | Primary auth |
|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` env |
| `openai` | `OPENAI_API_KEY` env; `OPENAI_BASE_URL` for Azure / compatible endpoints |
| `google` | `GOOGLE_API_KEY` env; or ADC on GCE/CloudRun |
| `ollama` | none (local) |

`workspace.yaml` may override (sealed-secret reference, Vault lookup, etc.) — that's the deployment story, not the provider story. `workspace-schema-v1.md` will define the exact shape when §M0 reaches it.

## Where the abstraction is wired

- **Topology schema** — unchanged. The existing `model.provider` string is already provider-agnostic; this design ratifies that choice.
- **Topology loader** — after archetype merge, validates that every distinct `provider` referenced in the topology is registered. Fails fast with a list of missing providers.
- **LangGraph compiler (M3)** — agent node functions receive a `ModelProvider` instance (resolved from the registry) and call `.complete(...)` / `.stream(...)`. No SDK imports outside provider modules.
- **Authoring swarms (M7, M8)** — use the same abstraction. A user who only has Ollama installed can still author skills locally.
- **`swarmkit eject` (M9)** — emits code that imports the same provider module. Ejected code depends on `swarmkit-runtime` at minimum; for full independence users can copy the provider they use. Document this trade.

## Non-negotiable invariant (lands with this PR)

**All LLM calls in the runtime go through `ModelProvider`.** Only files under `packages/runtime/src/swarmkit_runtime/model_providers/` may import Anthropic / OpenAI / Google / Ollama SDKs. Every other module receives a provider instance or a request object. Same rule as §8.5 for AGT imports. Enforced by review; a future lint rule could enforce mechanically.

## Test plan

- **Unit — ABC contract:** `MockModelProvider` implements the full interface; tests assert it satisfies the ABC and returns deterministic responses keyed on `(model, messages)`.
- **Unit — registry:** built-ins register on import; conflicting IDs raise; entry-point discovery is mocked via `importlib.metadata.entry_points`.
- **Unit — per-provider adapters:** each built-in adapter is tested with **recorded** responses (a tiny cassette format under `tests/fixtures/model-responses/`). No network in unit tests.
- **Integration — live APIs:** gated on `ANTHROPIC_API_KEY` etc. env vars with `pytest.mark.integration`. Skipped locally unless the key is present; run nightly in a separate CI workflow (not the PR pipeline).
- **Negative:** topology referencing a non-registered provider fails `swarmkit validate` with a clear error pointing at the offending agent.

## Demo plan

- `just demo-model-providers` (lands with the implementation PR, not this design PR): walks through each registered provider, does a single-shot completion with the prompt "Say ready" against its cheapest model, prints pass/fail. Missing creds are reported as `skipped` not `failed`. For `mock` and `ollama` (if running) it always runs.
- The implementation PR also ships `examples/model-choice/` — a two-agent topology where the leader runs Opus and the worker runs a local Ollama model. One YAML file, one README; proves cross-provider coordination.

## Open questions

- **Streaming protocol through LangGraph:** LangGraph has its own streaming primitives. Do we expose provider streaming directly or proxy through LangGraph's channel system? Tentative: proxy — keeps the rest of the framework consistent. Revisit at M3.
- **Tool-calling normalisation:** each provider's tool-call format differs (Anthropic's `tool_use` / `tool_result` blocks, OpenAI's function-calling schema, Google's parts system). Will be designed in `model-provider-tool-calling.md` before M3 implementation starts.
- **Cost tracking hooks:** every response carries `usage`, but who aggregates? Proposed: a `persistence` skill category consumer writes to the audit log via `GovernanceProvider.record_event`. Not framework-level infrastructure.
- **Rate limiting / retry:** start with a minimal exponential-backoff in each adapter. Global cross-provider throttling is future work.
- **Token budget enforcement:** ties to §8.6 governance overhead target. Providers expose `tokenize`; the judicial pillar (policy engine) can deny requests that would exceed budget. Cross-cutting; not part of this abstraction.

## Slot in the implementation plan

Insert a new **M2.5** between M2 (governance) and M3 (compiler):

- **M2.5 — Model provider abstraction**
  - Design note: `design/details/model-provider-abstraction.md` (this PR).
  - Impl PR 1: `ModelProvider` ABC + `MockModelProvider` + `AnthropicModelProvider`. Enough to unblock M3.
  - Impl PR 2: OpenAI + Google + Ollama adapters. Can land in parallel with M3 or after.
  - Impl PR 3: `model-provider-tool-calling.md` design note + implementation. Blocks M5 (MCP integration) because tool calls are the bridge.
  - Exit demo: `just demo-model-providers` green for every provider with creds present; integration tests nightly.

Without M2.5, M3's compiler PR would either hard-code Anthropic (violating principle §7 "Framework-aligned, not framework-locked") or re-invent this abstraction in-line. Landing the design now means M3 slots the compiler into an already-decided seam.
