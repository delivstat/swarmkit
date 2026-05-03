# Installation

## From source (current)

```bash
git clone git@github.com:delivstat/swarmkit.git && cd swarmkit
uv sync --all-packages    # Python deps
pnpm install              # TypeScript deps (optional, for schema validation)
```

## From PyPI

```bash
pip install swarmkit-runtime
```

Or install as a CLI tool with uv:

```bash
uv tool install swarmkit-runtime
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
