# Sample Run: Consciousness and Turiya (English)

## Input
```
What do the Upanishads say about consciousness? I've heard about
Turiya - the fourth state beyond waking, dreaming, and deep sleep.
Can you explain what that means and how to experience it?
```

## Command
```bash
cd /home/srijith/dev/swarmkit

export PATH="$HOME/.bun/bin:$PATH"
export VEDANTA_CHROMADB_DIR=/home/srijith/dev/swarmkit/examples/vedanta-advisor/knowledge/chromadb
export VEDANTA_NOTES_DIR=/home/srijith/dev/swarmkit/examples/vedanta-advisor/knowledge/notes
export OPENAI_API_KEY="${OPENROUTER_API_KEY}"
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"

SWARMKIT_MAX_TOOL_TURNS=200 \
SWARMKIT_MAX_PER_TOOL=100 \
SWARMKIT_MAX_PER_READ_TOOL=100 \
SWARMKIT_PROVIDER=openrouter \
SWARMKIT_MODEL=moonshotai/kimi-k2.6 \
uv run swarmkit run examples/vedanta-advisor/workspace advisor \
  --input "What do the Upanishads say about consciousness? I've heard about Turiya - the fourth state beyond waking, dreaming, and deep sleep. Can you explain what that means and how to experience it?"
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

This is one of the most beautiful and direct teachings in the Upanishadic tradition. Let me walk you through it.

### The Four "Mansions" of Consciousness

The **Mandukya Upanishad** — the shortest of the principal Upanishads, just twelve verses — maps the entire journey of human consciousness through three states, and then reveals a fourth that changes everything.

**1. Jagrat (Waking):** The state where the mind is *chanchala* — restless, outward-rushing. You identify with the body, interact with the world, and believe this solid reality is all there is.

**2. Svapna (Dreaming):** The outer world drops away, but the inner theater continues. Thoughts, memories, fears, desires play on the screen of the mind. The "I" is still there, experiencing.

**3. Sushupti (Deep Sleep):** Even the dream-images dissolve. The partition wall of ego seems to disappear. Yet something remains — because when you wake, you can say, *"I slept well."* Who knew? Who remembers the deep sleep?

These three are not separate rooms. They are shifting conditions of **one underlying awareness**.

### Then What Is Turiya?

Turiya literally means "the Fourth." But here's the crucial point: **it is not a fourth state alongside the other three.** It is the light by which all three are known.

Think of it this way: when you're watching a movie, the screen shows action scenes, then romance, then a dark scene. The scenes change, but the screen never does. Turiya is the screen — the witness-consciousness (*Sakshi*) in which waking, dreaming, and deep sleep arise and subside.

The Bhagavad Gita describes this same witness as the **upadrashta** (overseer) and **anumanta** (permitter):

> **उपद्रष्टानुमन्ता च भर्ता भोक्ता महेश्वरः**
> *"The overseer, the permitter, the sustainer, the experiencer, the great Lord..."* — Gita 13.23

And Krishna points to the paradox of this awareness:

> **या निशा सर्वभूतानां तस्यां जागर्ति संयमी**
> *"That which is night to all beings, in that the self-controlled one is awake."* — Gita 2:69

Ordinary people are asleep to Turiya — lost in identification with body, mind, and roles. The yogi is awake to the awareness that witnesses all states.

### How to "Experience" Turiya

This is where language gets tricky. You cannot *experience* Turiya the way you experience a dream or a sunset, because every experience happens *within* it. You are already Turiya right now. The problem is not absence but **overlooking** — like a man searching for his necklace while it rests on his own neck.

Still, the tradition offers pointers:

**1. The Witness Inquiry (Sakshi Bhava)**
Throughout your day, ask: *"Who is aware of these thoughts? Who noticed the anger arise? Who is aware of the breath?"* Don't answer with the mind — just keep turning attention back to the fact of awareness itself. The one who witnesses the waking state is the same one who witnesses dreams and deep sleep.

**2. Contemplate the Continuity**
When you wake from a dream, the dream-world vanishes but *you* remain. When you wake from deep sleep, that too vanishes but *you* remain. What is this "you" that survives all three? It cannot be the body (which was inactive in sleep), nor the mind (which was absent in deep sleep). It is the silent awareness in which all three appear.

**3. The AUM Meditation**
The Mandukya Upanishad maps AUM to the four states:
- **A** = waking (jagrat)
- **U** = dreaming (svapna)
- **M** = deep sleep (sushupti)
- **The silence after M** = Turiya

In meditation, as you chant or listen to AUM, pay attention not to the sounds but to the silence that follows. That silence is not empty — it is full, aware, and unchanging. It is the closest pointer to Turiya in sound-form.

**4. Neti Neti (Not This, Not This)**
The Upanishadic method of negation: "I am not the body, not the mind, not the emotions, not the roles I play..." What remains when everything negatable is set aside? That remainderless remainder is Turiya.

### A Story That Illustrates This

There's a simple but profound analogy from the tradition: A man dreams he is a king, then dreams he is a beggar. In both dreams, he suffers and rejoices. When he wakes, he realizes — *"I was neither king nor beggar. I was the one dreaming."*

Your waking life is not fundamentally different from a dream. It feels solid, but it passes. What doesn't pass? The awareness in which it all appears. That is Turiya. It is not somewhere else, not a state to achieve after years of practice. It is **right here, right now**, quietly illumining these very words as you read them.

---

If this resonates, you might also explore the **Yoga Sutras** on the three states, or the **Brihadaranyaka Upanishad's** dialogue on deep sleep — the tradition has many doorways into this same room. Would you like me to go deeper into any particular aspect — the AUM meditation, the witness practice, or how this relates to daily life?
