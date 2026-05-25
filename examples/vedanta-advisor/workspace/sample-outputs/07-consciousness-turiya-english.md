# Sample Run: Consciousness and Turiya (English)

## Input
```
What do the Upanishads say about consciousness? I've heard about
Turiya - the fourth state beyond waking, dreaming, and deep sleep.
Can you explain what that means and how to experience it?
```

## Model
Kimi K2.6 via OpenRouter

## Tool Calls (7 turns, 64.5s)

| Turn | Tool | Result |
|---|---|---|
| 1 | `brain-search` + `search-scripture` | 2KB — found `three-states-consciousness` wisdom block |
| 2 | `brain-get-relations` + `search-scripture` + `get-verse` | Edge traversal + Gita verses |
| 3 | `search-scripture` + `brain-search` + `get-verse` | Additional searches |
| 4 | `brain-search` + `search-scripture` + `get-verse` | More context |
| 5-6 | `get-verse` (gita:13:23, gita:2:69, gita:6:18) | Quote-level citations |
| 7 | `get-verse` (full detail for key verses) | Full commentaries for depth |

## GBrain Block Used
`three-states-consciousness` — matched on first search with the four states framework, Sakshi concept, and AUM mapping.

## Config
```
SWARMKIT_MAX_TOOL_TURNS=200
SWARMKIT_MAX_PER_TOOL=100
SWARMKIT_MAX_PER_READ_TOOL=100
```

## Response

Covers:
- The four mansions of consciousness (jagrat, svapna, sushupti, turiya)
- Turiya as the screen, not a fourth state — witness consciousness (Sakshi)
- Gita 13.23 (upadrashta) and 2.69 (yogi awake when others sleep) with Sanskrit
- Four practical methods: witness inquiry, continuity contemplation, AUM meditation, neti neti
- The dreaming king story as illustration
- Mandukya Upanishad's AUM mapping: A=waking, U=dreaming, M=deep sleep, silence=Turiya
