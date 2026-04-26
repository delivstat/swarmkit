---
title: Model selection guide — choosing and configuring LLM providers
description: How to pick models for leaders vs workers, configure per-agent models, and optimize for cost. Pricing comparison across providers.
---

# Model selection guide

SwarmKit supports 7 model providers out of the box. This guide
covers how to choose models for different agent roles, configure
them, and optimize for cost.

## Provider setup

Set the API key for any provider you want to use:

```bash
# Pick one or more
export ANTHROPIC_API_KEY=sk-...
export GOOGLE_API_KEY=AI...
export OPENAI_API_KEY=sk-...
export OPENROUTER_API_KEY=sk-or-...
export GROQ_API_KEY=gsk_...
export TOGETHER_API_KEY=...
# Ollama — always available, no key needed
```

SwarmKit auto-detects which providers are available from the
environment.

## Environment variables

| Variable | Scope | Format | Example |
|---|---|---|---|
| `SWARMKIT_PROVIDER` | All agents (global override) | Provider ID | `openrouter` |
| `SWARMKIT_MODEL` | All agents (global override) | Model name | `qwen/qwen3-235b-a22b` |
| `SWARMKIT_AUTHOR_MODEL` | Authoring only (`init`, `author`, `edit`) | `provider/model` | `openrouter/moonshotai/kimi-k2` |

**Important:** `SWARMKIT_PROVIDER` + `SWARMKIT_MODEL` override ALL
agents — leaders and workers alike. For per-role model selection,
use the topology YAML (see below).

### Quick start — one model for everything

```bash
export SWARMKIT_PROVIDER=openrouter
export SWARMKIT_MODEL=qwen/qwen3-235b-a22b
swarmkit run . my-topology --input "..."
```

### Authoring with a different model

```bash
# Topology runs use the YAML-defined models
# Authoring (init/author/edit) uses Kimi K2
export SWARMKIT_AUTHOR_MODEL=openrouter/moonshotai/kimi-k2
swarmkit init my-workspace/
```

## Per-agent model selection (topology YAML)

This is the recommended approach for production — each agent gets
the right model for its role:

```yaml
agents:
  root:
    id: root
    role: root
    model:
      provider: openrouter
      name: qwen/qwen3-235b-a22b     # large model for coordination
      temperature: 0.1
    children:
      - id: code-reviewer
        role: worker
        model:
          provider: openrouter
          name: qwen/qwen3-30b-a3b    # small model for tool calling
          temperature: 0.2
```

### Per-agent via archetypes

Set the model in the archetype — all agents using that archetype
inherit it:

```yaml
# archetypes/leader-archetype.yaml
defaults:
  model:
    provider: openrouter
    name: qwen/qwen3-235b-a22b
    temperature: 0.1

# archetypes/worker-archetype.yaml
defaults:
  model:
    provider: openrouter
    name: qwen/qwen3-30b-a3b
    temperature: 0.2
```

### Override in topology

An agent can override its archetype's model:

```yaml
- id: critical-reviewer
  role: worker
  archetype: worker-archetype
  model:
    provider: openrouter
    name: qwen/qwen3-235b-a22b   # upgrade this worker to large model
```

## Model priority resolution

SwarmKit resolves the model for each agent in this order:

1. `SWARMKIT_PROVIDER` / `SWARMKIT_MODEL` env vars (if set — overrides everything)
2. Agent's own `model:` block in the topology YAML
3. Archetype's `defaults.model:` block
4. First available provider in the registry

For production, don't use `SWARMKIT_PROVIDER` — it overrides your
carefully chosen per-agent models. Set models in the YAML and leave
the env vars unset.

## Pricing comparison (via OpenRouter, as of 2026-04)

### Large models (leaders, root supervisors, authoring)

| Model | Input /1M | Output /1M | Best for |
|---|---|---|---|
| **Qwen3 235B** | $0.46 | $1.82 | Cheapest large model, strong reasoning |
| DeepSeek R1 | $0.50 | $2.15 | Best reasoning-per-dollar, chain-of-thought |
| Kimi K2 | $0.57 | $2.30 | Strong agentic capability, long context |
| Claude Sonnet 4.6 | $3.00 | $15.00 | Highest quality, expensive |
| GPT-4o | $2.50 | $10.00 | Strong all-round, expensive |

### Small models (workers, tool calling, structured output)

| Model | Input /1M | Output /1M | Best for |
|---|---|---|---|
| **Qwen3 30B** | $0.08 | $0.28 | Cheapest capable worker, good tool calling |
| DeepSeek V3 | $0.32 | $0.89 | Good balance of price + quality |
| Gemini 2.5 Flash | $0.30 | $2.50 | Excellent tool calling, expensive output |
| Llama 3.3 70B | $0.10 | $0.30 | Good open-source alternative |

### Google Direct (alternative for Gemini)

| Tier | Input /1M | Output /1M | Notes |
|---|---|---|---|
| Free | $0.00 | $0.00 | 15 RPM, 1,500 RPD — good for testing |
| Standard | $0.30 | $2.50 | Same as OpenRouter |
| Batch/Flex | $0.15 | $1.25 | 50% cheaper, not available via OpenRouter |

## Recommended configurations

### Budget-optimized (all via OpenRouter)

Best cost-per-run. Single provider, single API key.

```yaml
# Leaders — Qwen3 235B ($0.46/$1.82)
model:
  provider: openrouter
  name: qwen/qwen3-235b-a22b

# Workers — Qwen3 30B ($0.08/$0.28)
model:
  provider: openrouter
  name: qwen/qwen3-30b-a3b
```

Estimated cost per topology run (3 leaders + 5 workers): **$0.10–0.20**

```bash
# Authoring uses the big model
export SWARMKIT_AUTHOR_MODEL=openrouter/qwen/qwen3-235b-a22b
```

### Quality-optimized (multi-provider)

Best output quality. Multiple API keys.

```yaml
# Leaders — Claude Sonnet
model:
  provider: anthropic
  name: claude-sonnet-4-6

# Workers — Gemini Flash
model:
  provider: google
  name: gemini-2.5-flash
```

Estimated cost per run: **$0.50–1.50**

### Development / testing

Zero cost during development.

```bash
# Free Google tier for everything
export SWARMKIT_PROVIDER=google
export SWARMKIT_MODEL=gemini-2.5-flash
```

Or fully local with Ollama (no API cost, no internet):

```bash
# Ollama — runs on your machine
export SWARMKIT_PROVIDER=ollama
export SWARMKIT_MODEL=llama3.3
```

### Balanced (recommended for Sterling workspace)

```yaml
# Root supervisor — needs strong reasoning for coordination
root:
  model:
    provider: openrouter
    name: qwen/qwen3-235b-a22b

# Sterling architect — needs domain knowledge + reasoning
sterling-architect:
  model:
    provider: openrouter
    name: qwen/qwen3-235b-a22b

# Retail expert — same
retail-expert:
  model:
    provider: openrouter
    name: qwen/qwen3-235b-a22b

# Config validator — needs precision, not creativity
config-validator:
  model:
    provider: openrouter
    name: deepseek/deepseek-chat    # DeepSeek V3, good at structured analysis

# Workers doing tool calls (search, API queries)
# Use the cheapest model that handles tool calling
workers:
  model:
    provider: openrouter
    name: qwen/qwen3-30b-a3b
```

```bash
# Authoring uses the large model
export SWARMKIT_AUTHOR_MODEL=openrouter/qwen/qwen3-235b-a22b
```

## Rate limits and concurrency

SwarmKit runs agents sequentially within a topology (root → leader
→ workers → next leader), so you're making 1 API call at a time
per run. Concurrency matters only when running multiple topologies
simultaneously via `swarmkit serve`.

| Provider | Published limits | Notes |
|---|---|---|
| OpenRouter | Credit-based, global rate limiting | No published RPM. Most generous for burst usage. |
| Google Free | 15 RPM, 1,500 RPD | Hits limits quickly during development |
| Google Paid | Tier-dependent (view in AI Studio) | Better but not published |
| Anthropic | 50–4,000 RPM depending on tier | Published in docs |
| Ollama | Unlimited (local) | Limited by your hardware |

**Recommendation:** OpenRouter is the most forgiving for
development — no hard RPM caps, credit-based throttling instead
of request-count limits. Google free tier is good for testing but
you'll hit the 1,500 RPD limit in a day of active development.

## Switching models at runtime

For quick experiments without editing YAML:

```bash
# Try a run with a different model
SWARMKIT_PROVIDER=openrouter SWARMKIT_MODEL=deepseek/deepseek-r1-0528 \
  swarmkit run . solution-review --input "..." --verbose

# Compare with another model
SWARMKIT_PROVIDER=openrouter SWARMKIT_MODEL=moonshotai/kimi-k2 \
  swarmkit run . solution-review --input "..." --verbose

# Check the logs to compare timing and output quality
swarmkit logs . --last 2
```

The `--verbose` flag shows per-agent timing so you can compare
model speed alongside quality.
