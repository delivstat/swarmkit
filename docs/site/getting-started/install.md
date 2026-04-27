# Installation

## From source (current)

```bash
git clone git@github.com:delivstat/swael.git && cd swael
uv sync --all-packages    # Python deps
pnpm install              # TypeScript deps (optional, for schema validation)
```

## From PyPI (coming soon)

```bash
pip install swael
```

## Verify

```bash
swael --help
swael validate examples/hello-swarm/workspace --tree
```

## Model providers

Swael auto-detects providers from environment variables:

| Provider | Env var |
|---|---|
| Anthropic | `ANTHROPIC_API_KEY` |
| Google | `GOOGLE_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| Groq | `GROQ_API_KEY` |
| Together | `TOGETHER_API_KEY` |
| Ollama | (always available) |

Override per-run: `SWARMKIT_PROVIDER=openrouter SWARMKIT_MODEL=meta-llama/llama-3.3-70b-instruct swael run ...`
