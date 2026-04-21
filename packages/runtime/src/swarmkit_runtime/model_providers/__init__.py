"""Model provider abstraction — the single seam through which the runtime
reaches any LLM (Anthropic, OpenAI, Google, Ollama, or custom).

See `design/details/model-provider-abstraction.md` for the full spec.

Non-negotiable invariant: **only this package may import LLM SDKs**
(`anthropic`, `openai`, `google-genai`, Ollama's HTTP client). Every other
module in `swarmkit_runtime` receives a `ModelProvider` instance or a
`CompletionRequest` and never touches a vendor SDK directly. Same rule
as `governance/` for AGT. See root CLAUDE.md invariant #4.

Implementation lands in M2.5 PRs (see design/IMPLEMENTATION-PLAN.md):
  - `ModelProvider` ABC + `MockModelProvider` + `AnthropicModelProvider`
  - `OpenAIModelProvider`, `GoogleModelProvider`, `OllamaModelProvider`
  - Registry + entry-point discovery + workspace.yaml overrides
  - Tool-calling normalisation (blocks M5)
"""
