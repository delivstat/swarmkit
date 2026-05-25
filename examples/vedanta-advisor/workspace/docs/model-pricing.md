# Model Pricing Notes

## Kimi K2.6 vs K2.5 — OpenRouter vs Moonshot Direct

| Model | | OpenRouter | Moonshot Direct |
|---|---|---|---|
| **K2.6** | Input | $0.73/1M | $0.95/1M |
| | Output | $3.49/1M | $4.00/1M |
| | Cached input | — | $0.16/1M (83% off) |
| **K2.5** | Input | $0.40/1M | $0.60/1M |
| | Output | $1.90/1M | $3.00/1M |
| | Cached input | — | $0.10/1M (83% off) |

### Recommendation

**For development/testing:** OpenRouter — 23-37% cheaper on list price, single API key for all models.

**For production at scale:** Moonshot Direct — automatic context caching kicks in for repeated system prompts. The Vedanta advisor uses the same long archetype prompt every query, so after the first call, input costs drop 83%. At 1000+ queries/day, Moonshot Direct wins.

### K2.6 vs K2.5 for this workspace

K2.6 is a thinking model — better reasoning but 2x the cost and much slower (60-90s per call due to internal chain-of-thought). K2.5 is faster and cheaper but weaker tool discipline.

| Use case | Recommended |
|---|---|
| Advisor (conversation) | K2.6 — reasoning quality matters for life guidance |
| Graph builder (batch) | K2.6 — deep interpretation of verses needs thinking |
| Citation verifier | K2.5 or DeepSeek Chat — mechanical comparison, no deep reasoning |
| Quality critique | DeepSeek Chat — cheapest, just reviewing two texts |
| Ingestion | DeepSeek Chat — structural normalization only |

### Cost per advisor query

With `detail=quote` verse fetching and GBrain wisdom blocks:
- K2.6: ~$0.01-0.05 per query (~15-50K input tokens)
- K2.5: ~$0.005-0.02 per query (same tokens, lower rate)

### Moonshot Direct setup

If switching to direct:
```
API base: https://api.moonshot.ai/v1
Model: kimi-k2.6 (or kimi-k2.5)
Key: from platform.kimi.ai
Min recharge: $1 to activate
```

OpenAI-compatible endpoint — just change `OPENAI_BASE_URL` and `OPENAI_API_KEY` in .env. No code changes needed.
