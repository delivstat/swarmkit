# Sample Run: Multi-Turn Chat — Memory, Depth, Language (English → Hindi)

## Test Plan
1. **Memory**: Does the advisor remember prior turns?
2. **Depth escalation**: Can the user go deeper on a topic?
3. **Language switching**: Can the user switch to Hindi mid-conversation?

## Command
```bash
printf "I have been feeling very angry at my brother. He betrayed my trust.\n\
Can you tell me more about the story you just mentioned?\n\
Now tell me the same thing in Hindi.\nexit\n" | \
uv run swarmkit chat examples/vedanta-advisor/workspace advisor
```

## Model
claude-sonnet-4 via OpenRouter (note: chat mode did not pick up SWARMKIT_MODEL=kimi-k2.6)

## Results

### Turn 1: Anger at brother's betrayal (22.2s, 5 tool calls)
GBrain matched sibling-rivalry wisdom block. Scripture search found anger/desire verses.

> I hear you — betrayal by a brother cuts especially deep because it breaks the trust that family bonds are supposed to hold sacred.
>
> **The texts teach that anger at betrayal is natural, but holding onto it poisons you more than the betrayer.** [...]
>
> The Mahabharata is essentially the story of brothers destroying each other — the Pandavas and Kauravas were cousins raised as brothers [...]
>
> **Pause before accusation. Ask three times whether you understood the act correctly before naming it betrayal; assumption is the mother of enmity.**

### Turn 2: "Tell me more about that story" (15.9s, 3 tool calls)
**Memory test: PASS** — advisor correctly referenced the Mahabharata story from turn 1 without re-asking.

> The Mahabharata centers on two sets of cousins — the five Pandava brothers [...] and their hundred Kaurava cousins led by Duryodhana. [...]
>
> The central betrayal happens when Duryodhana invites Yudhishthira to a rigged dice game. [...]
>
> The profound teaching isn't in the details of who did what to whom, but in what Krishna tells Arjuna when he's paralyzed by the prospect of fighting his own family.

Honest about limits: "the texts I have access to don't have the detailed Mahabharata story blocks"

### Turn 3: "Now tell me the same thing in Hindi" (31.8s, 1 tool call)
**Language switch test: PARTIAL PASS** — IndicTrans2 failed to load, model fell back to natural Hinglish.

> **Central betrayal tab hota hai** jab Duryodhana, Yudhishthira ko rigged dice game ke liye invite karta hai. [...]
>
> **Asli teaching** yeh nahi hai ki kisne kya kiya, but yeh hai ki Krishna ne Arjuna ko kya kaha [...]

## Verdict
- **Memory**: ✅ Pass — turn 2 referenced turn 1 story correctly
- **Depth escalation**: ✅ Pass — moved from teaching to narrative detail
- **Language switching**: ⚠️ Partial — Hinglish fallback worked, but IndicTrans2 needs model download

## Issues Found
1. **IndicTrans2 not loaded**: `Could not import module 'AutoModelForSeq2SeqLM'` — needs `transformers` package or model download
2. ~~**SWARMKIT_MODEL not respected in chat mode**~~: false alarm — was a test setup issue (`VAR=x cmd1 | cmd2` only sets var for cmd1). The runtime reads `SWARMKIT_MODEL` correctly; use `export` before piping.
3. **GBrain search gaps**: knowledge blocks exist but search queries in turn 2 didn't match well
