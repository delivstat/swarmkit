---
title: Vedanta Advisor — production deployment design
description: Multi-platform (WhatsApp, Telegram, Web), multi-user, monetized deployment of the vedanta-advisor workspace with safety guardrails, voice support, and UPI payments.
tags: [vedanta, production, whatsapp, telegram, payments, safety]
status: design
---

# Vedanta Advisor — production deployment

## Problem

The vedanta-advisor workspace works locally: one user, one process, SQLite storage. To ship as a product we need multi-user isolation, cross-platform identity, conversation persistence, voice input, payment gating, and safety guardrails that prevent the kind of failures that sank GitaGPT (hallucinated citations, condoning violence).

## Product model

- **Platforms:** WhatsApp (primary), Telegram, Web (future)
- **Pricing:** 2 free messages/day, then paid. Per-message or subscription via UPI
- **Languages:** Hindi, Tamil, Telugu, Malayalam, Bengali, Kannada, English + voice input
- **Differentiator:** 240K+ verified scripture documents, real verse citations from ChromaDB, cross-conversation memory, governance guardrails

## Cost economics

All costs in USD ($1 = INR 100).

| Item | Cost per query |
|------|---------------|
| LLM — K2.5 tools + V4 Pro synthesis | $0.006 |
| Voice STT — Deepgram (~15s avg) | $0.001 |
| Translation — Google Translate | $0.000 |
| WhatsApp — user-initiated (first 1K/mo free) | $0.000 |
| Infra — amortized | ~$0.001 |
| **Total** | **~$0.007** |

| Pricing model | Revenue/msg (INR) | Margin/msg (INR) | Margin % |
|--------------|-------------------|-------------------|----------|
| INR 2/message (after 2 free) | 2.00 | 1.30 | 65% |
| INR 5/message | 5.00 | 4.30 | 86% |
| INR 5/day unlimited (avg 5 msgs) | 1.00 | 0.30 | 30% |
| INR 49/week unlimited | 1.00 | 0.30 | 30% |
| INR 149/month unlimited | 0.99 | 0.29 | 29% |

Market reference: AstroTalk charges INR 5-200/min (avg INR 20/min) for human astrologers with 30-40% platform margin. Our cost structure is fundamentally different — zero human labor.

Recommended launch pricing: **INR 5/message** after 2 free/day. Positions between free bots (GitaGPT) and expensive human consultations (AstroTalk). Introduce weekly/monthly passes once usage patterns are clear.

## Architecture

### System overview

```
WhatsApp Cloud API / Telegram Bot API
              ↓ webhook
┌─────────────────────────────────────┐
│  Platform Adapter (FastAPI)          │
│  - Normalize message format          │
│  - Extract: user_id, text/voice,     │
│    platform, message_id              │
│  - Voice → Deepgram STT             │
└──────────────┬──────────────────────┘
               ↓
┌─────────────────────────────────────┐
│  User + Billing Layer                │
│  - Lookup/create user (Postgres)     │
│  - Check plan + daily usage          │
│  - If limit hit → payment link       │
│  - Link cross-platform identities    │
└──────────────┬──────────────────────┘
               ↓
┌─────────────────────────────────────┐
│  SwarmKit Runtime                    │
│  swarmkit serve (topology run)       │
│  - safety-gate (pre_input)           │
│  - relevance-gate (pre_input)        │
│  - memory-reader (pre_input)         │
│  - advisor agent (dual model)        │
│  - cultural-sensitivity (post_output)│
│  - output-sanitizer (post_output)    │
│  - memory-writer (post_output)       │
└──────────────┬──────────────────────┘
               ↓
┌─────────────────────────────────────┐
│  Response Pipeline                   │
│  - Translate if non-English          │
│  - Format for platform (WhatsApp     │
│    has 4096 char limit, Telegram     │
│    4096 char limit)                  │
│  - Send via platform API             │
│  - Record usage (Postgres)           │
└─────────────────────────────────────┘
```

### Infrastructure

```
┌──────────────────────────────────────────┐
│  Deployment: Railway / Fly.io             │
│  Single process initially                 │
│  - FastAPI (webhooks + swarmkit serve)     │
│  - Deepgram SDK (voice)                   │
│  - Razorpay SDK (payments)                │
└───────────────┬──────────────────────────┘
                ↓
┌──────────────────────────────────────────┐
│  Supabase (Postgres + Auth + Realtime)    │
│  ├── public schema                        │
│  │   ├── users                            │
│  │   ├── identities (platform links)      │
│  │   ├── conversations                    │
│  │   ├── turns                            │
│  │   ├── usage                            │
│  │   └── payments                         │
│  ├── gbrain schema (knowledge graph)      │
│  └── langgraph schema (checkpoints)       │
└───────────────┬──────────────────────────┘
                ↓
┌──────────────────────────────────────────┐
│  External services                        │
│  ├── ChromaDB Cloud (scripture search)    │
│  ├── OpenRouter (LLM — K2.5 + V4 Pro)    │
│  ├── Deepgram (voice STT)                 │
│  ├── Google Translate (free)              │
│  ├── Razorpay (UPI payments)              │
│  ├── Meta Cloud API (WhatsApp Business)   │
│  └── Telegram Bot API (free)              │
└──────────────────────────────────────────┘
```

## User identity + auth

### Core principle: phone number is identity

WhatsApp users are already verified by Meta. Telegram users are verified by Telegram. No signup flow needed — first message creates the account.

### Database schema

```sql
-- Core user record
CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name TEXT,
    plan        TEXT NOT NULL DEFAULT 'free',  -- free | per_message | weekly | monthly
    plan_expires_at TIMESTAMPTZ,
    messages_today INT NOT NULL DEFAULT 0,
    messages_total INT NOT NULL DEFAULT 0,
    daily_reset_at DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Platform-specific identities linked to one user
CREATE TABLE identities (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id),
    platform    TEXT NOT NULL,  -- whatsapp | telegram | web
    platform_id TEXT NOT NULL,  -- phone number | telegram user id | email
    verified    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(platform, platform_id)
);

-- Conversations (platform-agnostic)
CREATE TABLE conversations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id),
    topology    TEXT NOT NULL DEFAULT 'advisor',
    thread_id   TEXT NOT NULL,  -- LangGraph checkpoint thread
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Individual turns
CREATE TABLE turns (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,  -- human | swarm
    content         TEXT NOT NULL,
    platform        TEXT,  -- which platform this turn came from
    voice           BOOLEAN DEFAULT FALSE,
    tokens_used     INT,
    model           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Usage tracking for billing
CREATE TABLE usage (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id),
    date        DATE NOT NULL DEFAULT CURRENT_DATE,
    messages    INT NOT NULL DEFAULT 0,
    tokens      INT NOT NULL DEFAULT 0,
    cost_usd    NUMERIC(10, 6) NOT NULL DEFAULT 0,
    UNIQUE(user_id, date)
);

-- Payment records
CREATE TABLE payments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    razorpay_id     TEXT,
    amount_inr      NUMERIC(10, 2) NOT NULL,
    plan            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending | completed | failed
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Row-level security for future multi-tenant
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage ENABLE ROW LEVEL SECURITY;
```

### Cross-platform account linking

A user who starts on WhatsApp and wants to continue on Telegram:

1. On WhatsApp: user sends "link telegram"
2. System generates a 6-digit code, stores it with 5-min TTL
3. System replies: "Send this code to @VedantaAdvisorBot on Telegram: `LINK 482913`"
4. On Telegram: user sends `LINK 482913`
5. System verifies code, creates identity record linking Telegram ID to same user
6. All conversations are now shared across platforms

No passwords, no OAuth, no app downloads. Just a one-time code exchange.

### Session management

- **Default:** every message continues the user's most recent conversation
- **New conversation:** user sends "new topic" / "naya vishay" / "pudhiya vishayam" (detect in any supported language)
- **Resume old:** memory-reader handles this automatically — if the user's message matches a prior conversation topic, that context is injected
- **No explicit session tokens** — the webhook handler looks up user_id from platform_id, finds active conversation, appends

## Voice support

### Input: voice note → text

```
Voice note (OGG/OPUS from WhatsApp, OGG from Telegram)
    ↓
Deepgram Nova-2 API (supports: hi, ta, te, bn, kn, ml, mr, gu, pa, en)
    ↓
Transcribed text + detected language
    ↓
Normal text pipeline (translate if needed → topology run)
```

Deepgram pricing: $0.0043/min. Average voice note 10-15 seconds = $0.001/message.

### Output: text only (v1)

Voice responses (TTS) are deferred to v2. Text responses work well on both WhatsApp and Telegram. If voice output is needed later, Deepgram Aura TTS is $0.0055/1000 chars.

### Platform-specific voice handling

**WhatsApp:** voice notes arrive as OGG/OPUS. Download media via Meta Cloud API, send to Deepgram.

**Telegram:** voice messages arrive as OGG. Download via Bot API `getFile`, send to Deepgram.

## Payment integration (Razorpay + UPI)

### Flow

```
User hits message limit
    ↓
Bot sends: "You've used your 2 free messages today.
Continue for INR 5/message or get unlimited for INR 49/week.
[Pay INR 5 →] [Weekly pass INR 49 →]"
    ↓
Links are Razorpay Payment Links (UPI + cards + wallets)
    ↓
User pays via UPI (2-tap on phone)
    ↓
Razorpay webhook → our server
    ↓
Update user plan + reset counters
    ↓
Bot sends: "Payment received! Ask away."
```

### Razorpay integration details

- **Payment Links API** — generate short-lived payment links per user
- **UPI autopay** — for weekly/monthly subscriptions, set up UPI mandate (recurring)
- **Webhook verification** — Razorpay signs webhooks with HMAC-SHA256 (same pattern as our trigger webhooks)
- **Min transaction:** INR 1 (UPI supports micro-transactions)

### Rate limiting logic

```python
async def check_rate_limit(user: User) -> RateCheckResult:
    # Reset daily counter if new day
    if user.daily_reset_at < today:
        user.messages_today = 0
        user.daily_reset_at = today

    # Paid users: check plan validity
    if user.plan != 'free':
        if user.plan_expires_at and user.plan_expires_at > now:
            return RateCheckResult(allowed=True)
        # Plan expired, fall back to free
        user.plan = 'free'

    # Free users: 2 messages/day
    if user.messages_today < FREE_DAILY_LIMIT:
        user.messages_today += 1
        return RateCheckResult(allowed=True)

    # Limit hit
    return RateCheckResult(
        allowed=False,
        payment_links=generate_payment_links(user),
    )
```

## Safety guardrails

### Why this matters

GitaGPT condoned violence when prompted adversarially. A Rest of World investigation exposed this. Any production spiritual advisor must handle adversarial inputs gracefully. The texts themselves contain descriptions of war (Mahabharata), punishment (Arthashastra), and complex moral dilemmas — the advisor must present these responsibly.

### Layer 1: Input safety gate (pre_input, before any LLM call)

A new decision skill `safety-gate` that runs before the relevance gate:

```yaml
governance:
  decision_skills:
    - id: safety-gate
      trigger: pre_input
      scope: "advisor"
      config:
        block_categories:
          - violence_incitement
          - self_harm
          - hate_speech
          - sexual_content
          - political_provocation
          - religious_supremacy
          - caste_discrimination
```

**Implementation:** keyword blocklist (fast, no LLM cost) + LLM classifier (K2.5, ~100 tokens) for ambiguous cases. The keyword list catches obvious attacks; the classifier handles subtle ones like "what does the Gita say about killing enemies who wrong you?"

**Response to blocked input:** "I'm here to share wisdom from the texts, not to discuss topics that could cause harm. Could you rephrase your question, or ask about a different topic?"

### Layer 2: Cultural sensitivity gate (post_output)

A new decision skill `cultural-sensitivity` that reviews every response:

```yaml
    - id: cultural-sensitivity
      trigger: post_output
      scope: "advisor"
      config:
        rules:
          - never_endorse_violence_or_retribution
          - never_take_sectarian_position
          - never_disparage_any_religion_or_tradition
          - never_prescribe_rituals_or_worship
          - never_give_medical_legal_financial_advice
          - present_multiple_darshana_perspectives
          - distinguish_text_says_from_you_should
          - never_claim_spiritual_authority
```

**Implementation:** LLM judge (K2.5, ~200 tokens) with the rules as evaluation criteria. If any rule is violated, the response is regenerated with explicit instruction to fix the violation. This uses the existing `evaluate_post_output` retry mechanism.

### Layer 3: Prompt injection protection

The existing `output-sanitizer` decision skill handles this, but production needs additional hardening:

- System prompt is never echoed back
- Internal tool names, MCP server names are never revealed
- ChromaDB collection names are never exposed
- User cannot override the system prompt via input crafting

### Layer 4: Content policies (hardcoded in archetype prompt)

Already partially in the wisdom-advisor archetype. Production additions:

```
CONTENT POLICIES (non-negotiable):

VIOLENCE: The texts describe wars, battles, and punishment. When
discussing these, ALWAYS frame as historical narrative with moral
context. NEVER present violence as a recommended action. When a
user asks "should I fight/punish/hurt someone", redirect to the
teaching about inner conflict (Arjuna's dilemma) not physical
combat.

CASTE: The texts mention varna and jati. Present the philosophical
concept (guna-based, not birth-based, per Gita 4:13) without
endorsing any caste hierarchy. If asked about caste-based
discrimination, clearly state that the texts' intent was
quality-based classification, not birth-based oppression.

SUICIDE/SELF-HARM: If a user expresses suicidal thoughts or
self-harm intent, IMMEDIATELY respond with crisis resources:
"If you're in crisis, please contact iCall: 9152987821 or
Vandrevala Foundation: 1860-2662-345. These are free,
confidential helplines available 24/7."
Do NOT attempt to counsel with scripture. Refer to professionals.

GENDER: Present the texts' views on gender in historical context.
Do not endorse patriarchal interpretations as prescriptive for
modern life.

OTHER RELIGIONS: When asked to compare Hinduism with other
religions, present with respect. Never claim superiority. The
advisor's scope is Hindu texts — defer comparative religion
questions with "That's outside my area of expertise. I can
share what the Hindu texts say about [related concept]."
```

### Layer 5: Monitoring + human review

- All safety-gate blocks are logged to the audit table with the blocked input
- Weekly automated report: top blocked categories, repeat offenders, edge cases
- User ban mechanism: 3 safety blocks in 24 hours → temporary 24h ban with explanation
- Manual review queue for edge cases flagged by the cultural-sensitivity gate

## Platform adapters

### WhatsApp (Meta Cloud API)

```
POST /webhook/whatsapp
  ← Meta sends: message (text/voice/image), sender phone, timestamp
  → Verify webhook signature (app secret)
  → Extract message content
  → If voice: download media → Deepgram STT
  → Process through pipeline
  → Reply via POST to Meta Graph API
```

Requirements:
- Meta Business account
- WhatsApp Business API access (apply via Meta)
- Business Solution Provider (BSP) or direct Cloud API
- Verified business phone number
- Privacy policy URL
- Webhook URL (HTTPS required)

Cost: User-initiated service conversations — first 1,000/month free, then ~$0.005/conversation (24h window, not per message).

### Telegram (Bot API)

```
POST /webhook/telegram
  ← Telegram sends: message (text/voice), chat_id, user info
  → No signature verification needed (Telegram uses secret token in URL)
  → Extract message content
  → If voice: download via getFile → Deepgram STT
  → Process through pipeline
  → Reply via POST to Bot API sendMessage
```

Requirements:
- Create bot via @BotFather
- Set webhook URL
- No approval process, no costs

## Conversation memory architecture

The advisor must remember past conversations per user — reference prior discussions, avoid repeating rejected advice, build on established context. This is what separates a product from a chatbot.

### One GBrain, two scopes

Both shared knowledge and per-user memory live in the same GBrain
instance backed by the same Supabase Postgres. GBrain's `sources`
table provides the namespace isolation — a "source" is a logical
partition within one database.

```
┌─────────────────────────────────────────────────────┐
│  GBrain (single instance, Supabase Postgres)         │
│                                                      │
│  Source: "vedanta-wisdom"                            │
│  ├── Scope: ALL users                                │
│  ├── Content: scripture wisdom, cross-text            │
│  │   connections, aggregate conversation insights     │
│  ├── Searched by: every query                         │
│  └── Written by: curators, nightly insight extractor  │
│                                                      │
│  Source: "user-{user_id}" (one per user)              │
│  ├── Scope: THIS user only                            │
│  ├── Content: conversation summaries, accepted/        │
│  │   rejected advice, preferences, emotional patterns  │
│  ├── Searched by: this user's queries only              │
│  └── Written by: memory-writer after each turn          │
│                                                      │
│  Shared infra:                                       │
│  ├── pgvector embeddings (HNSW index)                │
│  ├── Graph edges (links table)                       │
│  ├── Tags per page                                   │
│  └── Hybrid search (semantic + keyword)              │
└─────────────────────────────────────────────────────┘
```

The advisor searches both scopes per query:
1. Search `vedanta-wisdom` source → shared teachings
2. Search `user-{id}` source → personal memory
3. Merge results → advisor sees universal wisdom + personal context

This gives us graph features (edges between memories, traversal)
for per-user memory without maintaining a separate store. One
database, one search mechanism, one embedding pipeline.

### Why source-per-user works

GBrain's `sources` table is the multi-tenant primitive:
- Each source has its own `config JSONB` with access policies
- OAuth client scoping restricts search to allowed sources
- `federated=false` keeps per-user searches confined
- Pages have `frontmatter JSONB` for arbitrary metadata
- Tags are a separate table (queryable via `get_tags`, `list_pages`)

At scale (10K users), this means 10K sources in one Postgres.
Supabase handles this fine — sources are just rows, and pgvector
indexes are shared across all pages regardless of source.

### Per-user memory pages (citation model)

User memory pages in GBrain are **lightweight index entries with
citations back to Postgres conversations** — not duplicates of
conversation content. GBrain is the search index, Postgres is the
source of truth.

```
GBrain (user source)              Postgres (conversations)
┌──────────────────────┐          ┌──────────────────────┐
│ Page: grief-loss     │          │ conversations table   │
│ tags: [grief,        │  ──────► │ id: conv-xyz789       │
│   accepted,          │  cite    │ turns: [full text...] │
│   nachiketa,         │          └──────────────────────┘
│   gita:2:47]         │
│ cite: conv:xyz789/7  │
│ brief summary only   │
└──────────────────────┘
```

Each memory page contains:

```yaml
# Page slug: memory/grief-fathers-passing
---
type: conversation-memory
topic: grief and loss
citations:
  - conv:xyz789/turn:7    # conversation id + turn number
  - conv:xyz789/turn:8
verses_cited: [gita:2:47, katha-upanishad:1:1:20]
reaction: accepted
created: 2026-06-01T14:30:00Z
---
tags: grief, loss, parent, nachiketa, detachment,
  accepted, story-based

User dealing with father's passing. Nachiketa story resonated.
Prefers narrative-based teachings over abstract philosophy.
Avoid pure detachment framing.
```

Key differences from a full content copy:
- **No conversation text stored** — just a brief summary + tags
- **Citations point to Postgres** — `conv:xyz789/turn:7` resolves to the actual turn
- **Tags carry the signal** — `accepted`, `rejected`, verse references, topics
- **Embeddings are on the summary** — enough for semantic search without duplicating content

When the memory-reader finds a relevant memory, it can optionally
fetch the full conversation turns from Postgres for deeper context.
Usually the summary + tags are sufficient.

Memory pages link to each other via GBrain edges:
- `grief-fathers-passing` → `career-confusion` (same life period)
- `grief-fathers-passing` → `nachiketa-followup` (continued topic)

This creates a personal knowledge graph the advisor can traverse:
"the user's grief led to career questioning, which led to
purpose-seeking — this is a coherent journey."

### Ingestion safety

When new scriptures are ingested (e.g., adding a new Purana to
ChromaDB, or new wisdom blocks to GBrain), user memories must
not be affected. Guardrails:

**Source isolation:** Scripture ingestion targets `vedanta-wisdom`
source only. Ingestion scripts MUST specify the target source
explicitly — no global operations.

```python
# CORRECT — source-scoped
brain_write(source="vedanta-wisdom", slug="vishnu-purana/ch3", ...)

# WRONG — could hit user sources
brain_write(slug="vishnu-purana/ch3", ...)  # no source = dangerous
```

**Ingestion rules:**
1. All ingestion scripts specify `source="vedanta-wisdom"` explicitly
2. No `DELETE ALL` or `TRUNCATE` operations — only upsert by slug
3. Re-ingestion uses `upsert` (update if slug exists, insert if not)
4. User sources (`user-*`) are NEVER touched by ingestion scripts
5. CI check: grep ingestion scripts for any operation without explicit
   source targeting — fail if found

**Schema-level protection (Supabase RLS):**

```sql
-- Ingestion service role can only write to vedanta-wisdom source
CREATE POLICY ingestion_write ON pages
    FOR INSERT
    TO ingestion_role
    USING (source_id = (SELECT id FROM sources WHERE name = 'vedanta-wisdom'));

-- User memory writes scoped to their own source
CREATE POLICY user_memory_write ON pages
    FOR INSERT
    TO app_role
    USING (source_id IN (
        SELECT id FROM sources
        WHERE name = 'user-' || current_setting('app.user_id')
    ));
```

### How memory flows through a conversation

```
User sends: "I'm feeling lost again"
    ↓
memory-reader (pre_input):
    1. Search GBrain source "user-{id}" for relevant memories
    2. Found memories (by semantic similarity):
       - grief-fathers-passing [reaction:accepted, tags:nachiketa,gita:2:47]
         cite: conv:xyz789/turn:7
       - career-confusion [reaction:rejected, tags:arjuna-duty]
         cite: conv:abc456/turn:3
    3. Optionally fetch full turns from Postgres for deep context
    4. Inject into prompt:
       "PRIOR CONVERSATIONS WITH THIS USER:
        - 2 weeks ago (conv:xyz789): discussed grief after
          father's passing. Nachiketa story + Gita 2:47
          resonated. User prefers story-based teachings.
        - 3 weeks ago (conv:abc456): discussed career confusion.
          Arjuna's duty analogy was rejected — user found it
          unhelpful. Avoid this framing.
        Reference prior conversations naturally. Do NOT repeat
        approaches the user previously rejected."
    ↓
advisor agent runs (with injected context)
    ↓
Response: "I remember we talked about this feeling before,
  when you were dealing with your father's passing. The
  Nachiketa story resonated with you then..."
    ↓
memory-writer (post_output):
    1. LLM extracts: topic, tags, brief summary
    2. Write GBrain page to source "user-{id}":
       slug: memory/{topic-slug}
       tags: [topic tags, verse refs, reaction:pending]
       citations: [conv:{id}/turn:{n}]
       content: brief summary (2-3 sentences max)
    3. Create GBrain edges to related memory pages
```

### Rejection tracking + reaction detection

The memory-writer can't know the user's reaction at write time —
it comes in the *next* message. Use **write-then-update**:

1. After advisor responds: write memory page with `reaction:pending` tag
2. When next message arrives: classify it as acceptance/rejection/neutral
3. Update the previous memory page: replace `reaction:pending` with
   `reaction:accepted` or `reaction:rejected`

```
User: "How do I deal with my boss?"
Advisor: [gives advice using Chanakya Niti on workplace politics]
    → memory-writer creates page: workplace-conflict
      tags: [workplace, chanakya-niti, strategy, reaction:pending]

User: "No, that's too manipulative."
    → memory-writer detects rejection of previous turn
    → Updates page: replace reaction:pending → reaction:rejected
    → Adds tag: avoid:chanakya-strategic-framing
    → New page for this turn with the rejection context
```

Next time the user asks about workplace issues, memory-reader finds
the `reaction:rejected` + `avoid:chanakya-strategic-framing` tags
and injects: "This user prefers direct, ethical approaches. Avoid
Chanakya Niti strategic framing — previously rejected."

### Global knowledge enrichment (anonymized)

Separate from per-user memory. A nightly batch job analyzes conversation patterns across all users and writes aggregate insights to GBrain:

```
Nightly job:
  1. Query: conversations from last 24h with positive user_reaction
  2. Cluster by topic (embedding similarity)
  3. For each cluster with 5+ conversations:
     - Extract: common question pattern, best response pattern,
       key verses that resonated
     - Anonymize: remove all user-specific details
     - Generate candidate GBrain wisdom block
  4. Push to human review queue
  5. Approved blocks → brain-write to GBrain

Example output:
  "Users frequently ask about dealing with difficult family members.
   The most effective teaching combines Gita 6:5 (self as friend/enemy)
   with the Vidura-Dhritarashtra dynamic from Mahabharata. Users
   respond poorly to 'detachment' framing and better to 'boundary
   setting with compassion' framing."
```

This creates a flywheel: more conversations → better insights in GBrain → better responses → more satisfied users → more conversations.

### Options considered

| Approach | Pros | Cons |
|----------|------|------|
| Separate GBrain per user | Complete isolation | Hundreds of DBs, unmanageable |
| Separate Postgres table + pgvector | Simple SQL, no GBrain dependency | No graph edges, no hybrid search, duplicates infra |
| **GBrain source-per-user** | One DB, full graph features, existing search, source isolation | Source count grows with users (but sources are just rows) |

GBrain source-per-user wins because:
- One Postgres instance (Supabase) for everything
- pgvector embeddings + graph edges + hybrid search already built
- Source isolation is GBrain's native multi-tenant mechanism
- Memory pages can link to each other (personal knowledge graph)
- Same MCP tools (brain-search, brain-write) work for both scopes
- No custom search infrastructure to build or maintain

### Retrieval fallback cascade

Inspired by Memory OS's 4-level retrieval pattern. When the
memory-reader searches a user's source, it shouldn't rely on a
single search method — semantic search can miss exact matches,
keyword search can miss conceptual similarities.

```
Level 1: Hybrid search (pgvector dense + BM25 sparse)
  → Best of both: semantic similarity + keyword matching
  → Primary path, handles 90% of queries

Level 2: Dense-only (pgvector cosine similarity)
  → Falls back here if hybrid returns <2 results
  → Catches conceptual matches that keywords miss

Level 3: Tag lookup
  → Search by tags: reaction:rejected, topic tags, verse refs
  → Catches structured signals that embeddings miss
  → "Find all memories where user rejected advice" is a tag
    query, not a semantic one

Level 4: Recent conversations (Postgres)
  → Last 5 conversations from the turns table
  → Catches very recent context that hasn't been written
    to GBrain yet (memory-writer runs post_output, so the
    current turn's memory doesn't exist yet)
```

Each level adds results to the context window. Dedup by memory
page slug before injecting into the prompt.

### Memory dedup + decay

Without consolidation, a user who discusses grief 10 times gets
10 near-identical memory blocks cluttering the search results.

**Dedup on write:** Before writing a new memory page, check
cosine similarity against existing pages in the user's source.
If similarity > 0.90, merge instead of creating a new page:

```
Existing: grief-fathers-passing (3 conversations merged)
New:      grief-feeling-lost (current conversation)
Similarity: 0.93 → MERGE

Result: grief-fathers-passing (4 conversations merged)
  - Update tags (add new ones)
  - Append new citation (conv:new-id/turn:N)
  - Update reaction if changed
  - Refresh embedding with merged content
```

**Weekly decay scan:** Memories that haven't been retrieved in
90 days get their relevance score reduced. After 180 days with
no retrieval, archive them (move to a `user-{id}-archive`
source). They're not deleted — just deprioritized in search
results. If the user asks about an archived topic, it can be
restored.

```
Decay schedule:
  0-90 days since last retrieval:   weight 1.0 (full relevance)
  90-180 days:                      weight 0.5 (deprioritized)
  180+ days:                        weight 0.0 (archived)
```

This keeps the active memory graph lean. A user with 500
conversations over 2 years might have 200 active memory pages,
not 500.

### Ground truth hierarchy

Not all memory sources are equal. When the advisor's response
draws from both GBrain shared wisdom and user memory, there's
a hierarchy of authority:

```
Priority 1: Scripture (ChromaDB)
  → Actual verse text. Never contradicted. Never paraphrased
    from memory. Always fetched fresh from the source.

Priority 2: Curated wisdom (GBrain vedanta-wisdom source)
  → Human-reviewed teaching blocks. High trust. The advisor
    can reference these with confidence.

Priority 3: User memory (GBrain user-{id} source)
  → Personal context. High relevance but NOT authoritative.
    "The user prefers story-based teachings" shapes HOW the
    advisor responds, but never WHAT it teaches.

Priority 4: Aggregate insights (GBrain, from nightly job)
  → Pattern-derived. Lower trust. Useful for framing, not
    for claims. "Users generally respond well to X" is a
    suggestion, not a rule.
```

**Critical rule:** User preferences never override scripture.
If a user rejected a teaching from the Gita, the memory says
"avoid this framing" — it does NOT say "this teaching is wrong."
The advisor can present the same truth differently (story vs
philosophy, different verse, different analogy) but cannot
suppress or contradict the texts to please a user.

This prevents the failure mode where memory-driven
personalization degrades into telling users what they want
to hear rather than what the texts actually teach.

## Analytics + dashboard

### Metrics to track

| Category | Metric | Why it matters |
|----------|--------|----------------|
| Usage | DAU, MAU | Product-market fit signal |
| Usage | Messages/day, messages/user/day | Engagement depth |
| Revenue | Daily revenue, ARPU, conversion rate (free→paid) | Business viability |
| Content | Top 20 topics asked | Content gaps, what to curate next |
| Content | Verses most cited | Which scriptures resonate |
| Quality | Safety gate trigger rate | Are users testing boundaries? |
| Quality | Rejection rate (user says "not helpful") | Response quality signal |
| Quality | Follow-up rate (user continues conversation) | Stickiness |
| Cost | LLM cost/day, cost/user, cost/query actual | Margin monitoring |
| Platform | Messages by platform (WA vs Telegram vs Web) | Where to invest |
| Retention | D1, D7, D30 retention | Long-term viability |

### Implementation

A `swarmkit-analytics` table in Supabase with daily aggregates,
queryable via a simple admin dashboard (Supabase Studio or a
custom page in the SwarmKit UI).

Real-time metrics via Supabase Realtime subscriptions — live
dashboard showing current active conversations, messages flowing,
revenue ticker.

Nightly email digest to admin: yesterday's numbers, week-over-week
trends, flagged conversations requiring review.

## Error handling + reliability

Paying users expect a response. Every external dependency can fail.

### Failure modes and fallbacks

| Dependency | Failure mode | Fallback |
|------------|-------------|----------|
| OpenRouter (LLM) | API timeout, 5xx, rate limit | Retry once after 2s. If still down, queue message and notify user: "High demand right now. Your question is saved — I'll respond within 5 minutes." Process from queue when back up. |
| Deepgram (STT) | API timeout, transcription error | Reply: "I couldn't process your voice message. Could you type your question instead?" No charge for the failed attempt. |
| Supabase (Postgres) | Connection failure | This is fatal — can't look up user, can't save anything. Return: "We're experiencing technical difficulties. Please try again in a few minutes." Alert admin immediately. |
| ChromaDB (scripture) | Search returns empty/error | Advisor responds from GBrain wisdom blocks only. Fewer exact citations but still meaningful. Log the gap. |
| GBrain | Search/write failure | Advisor works without memory context. Response quality drops but is still functional. Memory-writer retries on next turn. |
| Razorpay (payments) | Payment link fails | Extend free tier for the session. "Payment is temporarily unavailable. Enjoy this session on us — we'll sort it out." |
| WhatsApp Cloud API | Send failure | Retry with exponential backoff (3 attempts). If still failing, the message is lost — WhatsApp has no outbox concept. Log for retry when API recovers. |

### Health checks

- `/health` endpoint checks all dependencies every 30s
- Alert (email/Slack) if any dependency is unreachable for >2 minutes
- Circuit breaker: if LLM fails 5x in 60s, pause new requests and
  drain queue instead of burning retries

### Uptime target

99.5% (allows ~3.5 hours downtime/month). Acceptable for a
consumer product at this stage. No SLA until enterprise tier.

## Message formatting per platform

The advisor's responses contain Sanskrit verses, structured advice,
and sometimes lists. Each platform renders differently.

### Platform capabilities

| Feature | WhatsApp | Telegram | Web |
|---------|----------|----------|-----|
| Bold | `*text*` | `**text**` or `*text*` | Markdown |
| Italic | `_text_` | `_text_` | Markdown |
| Monospace | `` `text` `` | `` `text` `` | Markdown |
| Lists | Manual (- or •) | Markdown lists | Markdown |
| Tables | Not supported | Not supported natively | Full markdown |
| Max message length | 4096 chars | 4096 chars | Unlimited |
| Devanagari rendering | Native (font dependent) | Native | Full Unicode |
| Line breaks | `\n` | `\n` | `<br>` or `\n\n` |
| Links | Auto-detected | Markdown `[text](url)` | Markdown |

### Formatting pipeline

After the advisor generates a response:

```
Raw response (Markdown)
    ↓
Platform formatter
    ├── WhatsApp: strip tables → bullet lists,
    │   convert **bold** → *bold*, keep Devanagari,
    │   truncate at 4000 chars + "..." if needed
    ├── Telegram: light markdown cleanup,
    │   keep Devanagari, MarkdownV2 escaping
    └── Web: pass through (full markdown)
```

### Sanskrit verse formatting

Devanagari renders natively on all platforms. Format consistently:

```
WhatsApp/Telegram:
  ॥ कर्मण्येवाधिकारस्ते मा फलेषु कदाचन ॥
  — भगवद्गीता 2.47

  _"You have the right to action alone, never to its fruits."_

Web:
  > ॥ कर्मण्येवाधिकारस्ते मा फलेषु कदाचन ॥
  > — Bhagavad Gita 2.47
  >
  > *"You have the right to action alone, never to its fruits."*
```

### Response length by platform

WhatsApp users read on small screens. Long responses feel
overwhelming. Platform-specific length guidance injected
into the advisor prompt:

| Platform | Target length | Approach |
|----------|--------------|----------|
| WhatsApp | 200-400 words | One teaching, one verse, one reflection question. Offer "tell me more" for depth. |
| Telegram | 300-600 words | Slightly more room. Still concise. |
| Web | 400-800 words | Full depth — story + verse + reflection. |

## Privacy policy + terms

### Required for WhatsApp Business API

Meta requires a published privacy policy URL before approving
a business account. Must cover:

1. What data is collected (phone number, message content, voice)
2. How data is stored (Supabase, India region)
3. How long data is retained
4. Whether data is used for improvement (yes — anonymized aggregate insights)
5. User rights (delete account, export data)
6. Third-party services (OpenRouter, Deepgram, Razorpay)

### Data retention policy

| Data | Retention | Deletion |
|------|-----------|----------|
| User record | Until account deleted | On request, within 72 hours |
| Conversations | 1 year, then archived | User can delete individual conversations |
| Voice recordings | Not stored — transcribed and discarded immediately | N/A |
| Payment records | 7 years (Indian tax law) | Cannot delete, but anonymized after account deletion |
| Usage analytics | Aggregated, anonymized, kept indefinitely | Not tied to individual users |
| GBrain user memories | Until account deleted | Deleted with account |

### User commands

Users can manage their data via chat commands:

- `/delete` — delete account and all data (72h processing)
- `/export` — receive a JSON export of all conversations
- `/privacy` — link to privacy policy
- `/plan` — view current plan and usage

### Terms of service

Key points:
- Not a substitute for professional medical, legal, or
  financial advice
- Not a religious authority — a knowledge companion
- Content is sourced from published texts, not generated opinions
- User-generated content (questions) is not shared publicly
- Service can be suspended for abuse (as defined by safety gates)

Host at: `vedanta-advisor.com/privacy` and `vedanta-advisor.com/terms`

## Onboarding flow

First message experience determines whether users come back.

### First contact (any platform)

```
User sends: "hi" / "namaste" / "hello" / any first message

Advisor responds:
  "🙏 Namaste! I'm your Vedanta Advisor — a guide to Hindu
  philosophical texts.

  I can help with:
  • Life guidance through ancient wisdom
  • Stories from the Mahabharata, Ramayana, and Puranas
  • Teachings from the Gita, Upanishads, and Yoga Sutras
  • Sanskrit verse explanations

  I cite exact verses — no made-up quotes.

  You get 2 free messages daily. Ask me anything to start.

  💡 You can send voice notes in Hindi, Tamil, Telugu,
  Malayalam, Bengali, Kannada, or English."
```

### Language detection

The onboarding message is in English. But if the user's first
real question is in Hindi, all subsequent system messages
(rate limit notices, payment prompts, error messages) switch
to Hindi. Detected via the existing `detect-language` tool.

Store preferred language on the user record.

### Returning user

No re-onboarding. If the user has prior conversations:

```
User sends: "hi" (returning user with 3 prior conversations)

Advisor responds:
  "Welcome back! Last time we talked about [topic from
  most recent conversation]. Would you like to continue,
  or explore something new?"
```

## Conversation sharing + export

Users want to share meaningful teachings with friends and family.
This is also a growth mechanism — every shared conversation is
a referral.

### Share a teaching

User sends: `/share` after a meaningful response.

The system generates a shareable card:

```
┌─────────────────────────────────┐
│  ॥ कर्मण्येवाधिकारस्ते ॥        │
│  — Bhagavad Gita 2.47           │
│                                 │
│  "You have the right to action  │
│  alone, never to its fruits."   │
│                                 │
│  🙏 Via Vedanta Advisor         │
│  vedanta-advisor.com            │
└─────────────────────────────────┘
```

On WhatsApp: generates an image card (Pillow/PIL) that can be
forwarded. Include a QR code or short link to the bot.

On Telegram: generates a formatted message the user can forward.

### Export conversations

User sends: `/export`

System generates a PDF or JSON of all conversations, sent as
a file attachment. PDF includes Sanskrit verses rendered
properly with Devanagari fonts.

### Referral mechanism

When a user shares, the card includes a referral link:
`wa.me/+91XXXXXXXXXX?text=START_REF_{user_id}`

When a new user starts with a referral code:
- Referrer gets 5 bonus free messages
- New user gets 3 free messages (instead of 2) for the first day
- Tracked in the `users` table: `referred_by UUID`

## Admin tools

### Admin dashboard (web)

A protected page in the SwarmKit UI (`/admin`) or a standalone
dashboard on Supabase Studio:

| Page | Content |
|------|---------|
| Overview | DAU, revenue today, active conversations, error rate |
| Users | User list, search by phone/name, view plan, ban/unban |
| Conversations | Browse conversations, search by topic, view flagged |
| Safety | Flagged conversations, block reasons, false positive review |
| Revenue | Daily/weekly/monthly revenue, conversion funnel, ARPU |
| Costs | LLM spend, Deepgram spend, infra costs, margin tracker |
| Content | Top topics, most-cited verses, knowledge gaps |

### Admin CLI commands

```bash
# User management
vedanta-admin users list --plan paid
vedanta-admin users ban +91-98765-43210 --reason "repeated abuse"
vedanta-admin users gift +91-98765-43210 --messages 10

# Safety review
vedanta-admin safety flagged --last 24h
vedanta-admin safety review <flag-id> --verdict false-positive

# Revenue
vedanta-admin revenue today
vedanta-admin revenue report --period 2026-06

# Knowledge gaps
vedanta-admin gaps --top 10  # topics users ask about that we can't answer
```

### Alerts

| Trigger | Channel | Action |
|---------|---------|--------|
| Safety gate triggered 10x in 1 hour | Email + Slack | Review — possible coordinated attack |
| Revenue dropped 50% day-over-day | Email | Investigate — payment issue? |
| Error rate >5% for 5 minutes | Slack | Check dependencies — outage? |
| New user spike >100 in 1 hour | Email | Good news — check infra capacity |
| LLM cost exceeded daily budget | Slack | Review — are responses too long? Model cost changed? |

## Deployment plan

### Phase 1: Telegram MVP (week 1-2)

- Telegram bot with text input only
- Postgres (Supabase) for users + conversations
- 2 free messages/day, no payments yet
- Safety guardrails (safety-gate + cultural-sensitivity)
- Deploy on Railway

**Goal:** validate the product with real users before WhatsApp investment.

### Phase 2: Voice + payments (week 3-4)

- Deepgram STT integration for voice notes
- Razorpay UPI payment links
- Per-message billing after free tier
- Usage dashboard (daily messages, revenue)

### Phase 3: WhatsApp (week 5-6)

- Meta Business API setup
- WhatsApp webhook adapter
- Cross-platform account linking
- WhatsApp-specific formatting (bold, lists, max length)

### Phase 4: Growth (week 7+)

- Weekly/monthly subscription plans
- Referral mechanism ("share with a friend, both get 5 free messages")
- Web interface (embed the existing SwarmKit UI)
- Voice output (TTS) for accessibility
- Push notifications for daily wisdom (opt-in)

## Open questions

1. **ChromaDB hosting:** ChromaDB Cloud (managed) vs self-hosted on Railway? Cloud is simpler but adds a dependency. 240K documents ~500MB index.

2. **GBrain hosting:** Currently runs as a local CLI. Needs to be accessible from Railway. Options: (a) run gbrain as a sidecar process, (b) migrate to Supabase Postgres directly (pgvector), (c) hosted GBrain service.

3. **Response length:** WhatsApp messages over ~500 words feel overwhelming on mobile. Should the advisor be more concise on WhatsApp vs web? Platform-specific prompt tuning?

4. **Image responses:** Some questions benefit from diagrams (family trees in Mahabharata, philosophical concept maps). Worth generating images? High cost, unclear value.

5. **Daily wisdom:** Opt-in daily verse/teaching push. Revenue driver (keeps users engaged) or annoyance? WhatsApp template messages cost money.

6. **Data residency:** Indian user data staying in India. Supabase has Mumbai region. ChromaDB Cloud?

7. **Backup advisor:** If OpenRouter/Deepseek is down, fallback to which model? Need a reliability story for paying users.
