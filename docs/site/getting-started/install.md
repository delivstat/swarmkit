# Installation

## Install the CLI (recommended)

```bash
uv tool install swarmkit-runtime

# With serve-mode dependencies (HTTP server, JWT auth, cron triggers)
uv tool install swarmkit-runtime --with "swarmkit-runtime[serve]"

# With serve mode + the hosted web UI (the swarmkit-webui portal that `swarmkit serve` hosts)
uv tool install swarmkit-runtime --with "swarmkit-runtime[serve,ui]"
```

This installs `swarmkit` as a globally available CLI tool in an isolated environment — no virtual env needed, no system Python pollution. `uv` is the recommended way to install and maintain SwarmKit. The `[serve]` extra adds the HTTP server; `[ui]` adds the web portal on top (absent ⇒ `swarmkit serve` runs headless, API only). To add an extra to an existing install, re-run the command — `uv tool install` upgrades in place.

## From source

```bash
git clone https://github.com/delivstat/swarmkit.git && cd swarmkit
uv sync --all-packages    # Python deps
pnpm install              # TypeScript deps (optional, for UI + schema validation)
```

## Docker

```bash
docker run -v ./workspace:/workspace \
  -e OPENROUTER_API_KEY=$OPENROUTER_API_KEY \
  -p 8000:8000 \
  ghcr.io/delivstat/swarmkit:latest
```

## Verify

```bash
swarmkit --help
swarmkit validate examples/hello-swarm/workspace --tree
```

## Model providers

Providers are declared as YAML (`kind: ModelProvider`) and register when they are **ready** — their key is in the environment, or they need no key:

| Provider | Env var |
|---|---|
| Anthropic | `ANTHROPIC_API_KEY` |
| Google | `GOOGLE_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| Groq | `GROQ_API_KEY` |
| Together | `TOGETHER_API_KEY` |
| Ollama | none — `OLLAMA_BASE_URL` to point elsewhere (default `http://localhost:11434`) |
| rkllama, llama-server, openvino-model-server, mlx-lm, lemonade | none — local runtimes, each with a `*_URL`/`*_HOST` env var |

`swarmkit providers list` shows every provider and what it is waiting for. Any OpenAI- or Ollama-compatible endpoint is a YAML file in `<workspace>/providers/` — see the [model provider reference](../reference/model-provider.md).

Override per-run: `SWARMKIT_PROVIDER=openrouter SWARMKIT_MODEL=meta-llama/llama-3.3-70b-instruct swarmkit run ...`
