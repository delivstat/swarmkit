# 03 — Provider seams

Scope: the pluggable provider interfaces. SwarmKit's extension philosophy is "skills are the only
extension primitive," but these provider seams are how vendors/backends plug in.

| Seam | Interface (file) | Built-ins | Selected by |
|---|---|---|---|
| **ModelProvider** | `model_providers/_registry.py:16` (`ModelProviderProtocol`: `complete`, `supports`, `provider_id`, opt. `stream`/`tokenize`) | Anthropic, OpenAI, Google, Ollama, OpenRouter, Groq, Together, Mock (8) | `workspace.model_providers[]` (`class` path + `provider_id` + `config`) **and** env auto-register (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`, `GROQ_API_KEY`, `TOGETHER_API_KEY`; Ollama + Mock always on) |
| **GovernanceProvider** | `governance/__init__.py:279` (`evaluate_action`, `verify_identity`, `record_event`, `get_trust_score`, `evaluate_decision_skill`) | AGT, Mock, SkillBacked (wrapper) | `workspace.governance.provider` (`agt`/`mock`/`custom`) |
| **AuditProvider** | `audit/_provider.py:20` (`record`, `query`, `count`; append-only — no update/delete) | Mock, SQLite | `storage.audit.backend` (planned; currently sqlite hardcoded default) |
| **NotificationProvider** | `notifications/_provider.py:21` (`notify(NotificationEvent)`) | Terminal, Webhook, Slack, Discord, Telegram (5) | code-registered (no workspace schema yet); events: `hitl_requested`, `run_ended_error`, `skill_gap_surfaced` |
| **ContextCompressor** | `compression/_base.py` (`compress(text, ref)`, `name`, `reversible`) | Columnar (lossless), HeadTail (reversible-lossy), plugin (class path) | `workspace.context_compression.backend` + env override |
| **AuthProvider** | `auth/_provider.py` (`authenticate(AuthRequest)`) | None, APIKey, JWT | `server.auth` (see [02](02-serve-api.md); **not in schema**) |
| **SecretsProvider** (credentials) | `workspace.credentials[]` `{source, config}` | env, file, hashicorp-vault, aws-secrets-manager, gcp-secret-manager, azure-key-vault, plugin | per-entry `source` |

ModelProvider uses lazy imports (`__getattr__`) so missing vendor SDKs don't break load.
`ProviderRegistry.resolve(provider_id, model)` selects per agent.

## Control-plane implications

- **Model providers:** the panel should know *which providers/models each instance can serve*
  (capability advertisement) to route runs and aggregate usage/cost by `(provider, model)`. Cost
  extraction is plumbed (`run_usage.cost_usd`, `CircuitBreakerTracker.add_cost`) but providers
  don't populate `cost_usd` yet — a gap for fleet cost analytics.
- **Governance + audit:** for a fleet, policy + identity + audit want to be **central**, not
  per-instance SQLite. AGT's FlightRecorder (hash-chained append-only) is the model; a Postgres
  audit backend that all instances append to is the natural target ([04](04-persistence-state.md)).
- **Notifications:** all instances fire the same events → the panel (or a wrapper) must **dedup**
  by event id; or centralize dispatch. No schema config today.
- **ContextCompressor / Auth / Secrets:** per-instance config; the panel manages these as part of
  the per-instance `workspace.yaml` it versions/pushes ([07](07-schema.md)). Secrets must stay
  referenced (never literal) — the panel coordinates a central secret store, not secret values.
- **The provider pattern (`class` path + `config`) is the precedent** for how the control plane
  itself should register pluggable backends (e.g. a fleet aggregation sink).
