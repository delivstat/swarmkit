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
