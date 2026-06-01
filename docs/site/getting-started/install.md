# Installation

## From PyPI (recommended)

```bash
# Using uv (fastest — installs as a CLI tool, no venv needed)
uv tool install swarmkit-runtime

# Or with pip
pip install swarmkit-runtime

# With serve mode dependencies (HTTP server, JWT auth, cron triggers)
pip install swarmkit-runtime[serve]
```

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

SwarmKit auto-detects providers from environment variables:

| Provider | Env var |
|---|---|
| Anthropic | `ANTHROPIC_API_KEY` |
| Google | `GOOGLE_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| Groq | `GROQ_API_KEY` |
| Together | `TOGETHER_API_KEY` |
| Ollama | (always available) |

Override per-run: `SWARMKIT_PROVIDER=openrouter SWARMKIT_MODEL=meta-llama/llama-3.3-70b-instruct swarmkit run ...`
