# Sample Run: Dealing with Injustice (English)

## Input
```
Life has been really unfair to me. I work harder than everyone else but
I never get the recognition. People with connections and privilege get
ahead while I'm stuck. How do I deal with this injustice without
becoming bitter?
```

## Model
Kimi K2.6 via OpenRouter

## Tool Calls (8 turns, 121.6s)

| Turn | Tool | Result |
|---|---|---|
| 1 | `brain-search` (injustice, unfair, recognition) | 5KB — matched Karna story block |
| 2 | `brain-search` + `search-scripture` | Additional matches |
| 3 | `search-scripture` (karma, injustice) | 1KB |
| 4 | `get-verse` (gita:2:47) detail=quote | 170B |
| 5 | `brain-search` (detachment) + `search-scripture` | 3KB + 2KB |
| 6 | `get-verse` (gita:4:20) + `brain-get-relations` | 211B + 110B (edge traversal) |
| 7 | `get-verse` + `brain-search` | 211B + 33B |
| 8 | `get-verse` (gita:2:47) | 170B |

## GBrain Blocks Used
- `karna-story` (knowledge) — Karna's full arc of rejection and loyalty
- `detachment-from-outcomes` (wisdom) — karma yoga teaching
- Graph edge traversal via `brain-get-relations`

## Response

I hear you. That feeling — of pouring yourself into something and watching others glide past on connections you don't have — is one of the oldest wounds in the world. The texts don't dismiss it. They know it intimately.

Let me tell you about **Karna**.

### The Warrior Nobody Wanted

Karna was born with divine armor and earrings, the son of the sun god himself. But he was raised by a charioteer — a low-status position in that society — and the world never let him forget it.

When he showed up at the archery tournament where the Kuru princes were displaying their skills, Karna matched Arjuna shot for shot. The crowd was stunned. But the moment he was recognized as not being a kshatriya by birth, he was **mocked and thrown out**. Drona, the teacher, refused to teach him.

At every turn, Karna's excellence was met with rejection. Not because he lacked skill. Because he lacked the *right pedigree*.

His bitterness drove him into the arms of Duryodhana — the one man who offered him recognition — and that loyalty, however understandable, led him to side with injustice.

Karna's tragedy wasn't that he worked hard and wasn't recognized. It was that he let the world's blindness determine *where he placed his loyalty*.

### What the Gita Says

> **कर्मण्येवाधिकारस्ते मा फलेषु कदाचन**
> *"Your right is to action alone, never to its fruits."* *(Gita 2:47)*

The recognition, the promotion, the validation — those are *fruits*. They depend on variables outside you. But the action — the effort, the skill, the integrity — that is entirely yours.

> **त्यक्त्वा कर्मफलासङ्गं नित्यतृप्तो निराश्रयः**
> *"Having abandoned attachment to the fruits of action, ever-content, depending on nothing..."* *(Gita 4:20)*

### Three Practical Steps

1. **Separate the work from the reward** — Do the work because it builds your skill, your character, your *tejas* (inner fire). The recognition is not your department.
2. **Find your "Duryodhana" carefully** — When you've been overlooked, any recognition feels like water in a desert. Be careful who you let define your worth.
3. **Let the bitterness become fuel, not poison** — Like Shiva drinking the ocean's poison and transforming it into power. Your anger at injustice is not wrong. But will it become poison that corrodes you, or fire that forges you?

*"Will you let their blindness make you blind to your own worth?"*
