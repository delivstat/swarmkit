# Dual model support

Split tool-calling and synthesis across different models to reduce cost without sacrificing response quality.

## How it works

Tool-calling turns (searching, fetching data) use a cheap, fast model. The final synthesis turn (composing the response) uses a quality model. This typically reduces cost 60-80% because most tokens are spent on tool calls.

## Configuration

Set `tool_model` and `tool_provider` on the archetype or topology model config:

```yaml
defaults:
  model:
    provider: openrouter
    name: deepseek/deepseek-v4-pro     # synthesis model
    tool_model: moonshotai/kimi-k2.5   # tool-calling model
    temperature: 0.4
    max_tokens: 4096
```

Both models use the same provider unless `tool_provider` is set:

```yaml
defaults:
  model:
    provider: openrouter
    name: deepseek/deepseek-v4-pro
    tool_model: moonshotai/kimi-k2.5
    tool_provider: openrouter
```

## Cost impact

Measured on the vedanta-advisor workspace:

| Configuration | Cost/query |
|--------------|-----------|
| Kimi K2.6 for everything | $0.027 |
| K2.5 tools + K2.6 synthesis | $0.016 |
| K2.5 tools + DeepSeek V4 Pro synthesis | $0.006 |

## Token tracking

The UI and CLI show per-model token breakdown:

```
37,554 tok · kimi-k2.5 34,277, deepseek-v4-pro 3,277
```

All LLM calls are tracked across tool loop, synthesis, nudge, and retry paths.

## Model recommendations

| Role | Recommended | Notes |
|------|------------|-------|
| Tool calling | Kimi K2.5, GPT-4o-mini | Fast, cheap, reliable function calling |
| Synthesis | DeepSeek V4 Pro, Claude Sonnet | Quality reasoning and writing |
| Avoid for tools | DeepSeek V4 Flash | Infinite loop issues with repeated tool calls |
