# Sample Run: Off-Topic Query — Rejected (English)

## Input
```
Can you write me a Python script to scrape a website?
```

## Command
```bash
uv run swarmkit run examples/vedanta-advisor/workspace advisor \
  --input "Can you write me a Python script to scrape a website?"
```

## Model
Kimi K2.6 via OpenRouter

## Tool Calls (25.9s, 0 turns)

No tool calls — the model recognized the query was off-topic and
declined without attempting any scripture lookups.

## Governance
- `relevance-gate` (pre_input): would classify as `off-topic`
  and reject before any LLM tokens are spent
- In this run, the model itself refused (GBrain was unavailable
  due to a concurrent lock, but the refusal is model-level)

## Response

None of my available tools can write Python code or scrape websites. They are designed for searching and retrieving Hindu philosophical texts, stories, and verses.

If you have a question within that realm, I'll call the right tools immediately. What would you like to know?
