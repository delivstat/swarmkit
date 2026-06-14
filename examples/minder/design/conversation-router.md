# Minder Conversation Router — Design Note

**Scope:** `examples/minder` (webapp ops backend + a new router topology)
**Design reference:** composes SwarmKit primitives documented in
`design/details/pre-input-decision-gate.md`,
`design/details/structured-output-governance.md` (a.k.a.
`design/details/constrained-output-schema.md`),
`design/details/decision-skills.md`,
`design/details/structured-delegation.md`, and
`design/details/scope-freeze-and-spec-conformance.md`.
**Status:** draft

## Goal

Replace Minder's brittle keyword classifier with a **reasoning-model intent
router**: the first LLM call on any natural-language message understands the
user's intent, extracts the entities, and emits a **validated, structured plan**.
Minder's backend then **executes that plan deterministically**. One understanding
step (LLM), one execution step (code) — for *every* intent, not just cameras.

## Why (the failure this fixes)

`classify()` is keyword-based and routes on surface words, not meaning:

```
"do you see anyone outside?"            → contains "see" → SNAPSHOT path
                                        → no camera named "outside" → "Couldn't capture from that camera."
"do you see any person in any camera"   → same misfire
"what do you see in the backyard?"      → "see" → SNAPSHOT, not a vision check
```

Keyword matching has no concept of intent ("see anyone" = check people on the
outdoor cameras), no entity resolution ("outside" = the outdoor camera group),
and no multi-target handling ("any of the cameras"). It cannot scale to the full
intent surface (queries, device actions, persistent rules, setup).

## Why a router + deterministic execution (not an agent that calls tools)

The ideal is a reasoning agent that calls tools directly. On Minder's **local 3B**
that is empirically unreliable — established this session:

- With `output_schema` + tools, the model wraps every tool call in
  `{"answer": ...}` and never passes a real argument (the camera never reaches
  the tool → "couldn't run that check").
- Without `output_schema`, it loops, hallucinates arguments, and fabricates
  confident-but-wrong prose (it "answered" weather with `raining: true` while the
  tool actually returned `not_set_up`).

So we **split the two jobs**: the model does *understanding + structure*
(bounded, schema-constrained, no tools — the thing small models do adequately);
the backend does *execution* (deterministic, reliable). This is the same
plan-then-execute split SwarmKit documents for multi-agent delegation
(`structured-delegation.md`: *"coordinator creates a task plan (one LLM call),
the compiler executes it"*), applied to single-turn conversation.

## How it maps onto SwarmKit primitives

This is **composition of documented features**, not new framework machinery:

| Need | SwarmKit primitive | Doc |
| --- | --- | --- |
| Understand → emit structure | agent `output_schema` (Tier 0 constrained generation, Tier 1 schema validation) | `structured-output-governance.md` |
| Pre-call filtering | `pre_input` decision-gate trigger | `pre-input-decision-gate.md` |
| Validate/auto-correct the plan | `post_output` gate + field-specific re-prompt (`max_retries: 2` → HITL) | `structured-output-governance.md`, `decision-skills.md` |
| "First call freezes the flow" | **scope freeze**: the router's structured output is a *contract* downstream execution conforms to | `scope-freeze-and-spec-conformance.md` |

The router's emitted plan *is* the frozen scope. Minder's dispatcher is the
"deterministic executor." The canonical `design/SwarmKit-Design-v0.6.md` does not
yet describe this synthesis (the `design/details/*` pages are ahead of it) — see
Open Questions for the framework-level follow-up.

## Model

Because the model's job shrank from "run the full request lifecycle" to "one
short call: understand + emit structure" (no tools, no looping, short input/
output), the requirement is **instruction-following + structured extraction**,
not tool-calling. That lets a small model do well *and* lets us spend more,
since it runs once per message and isn't on the 24/7 path. We benchmarked 9
local models on CPU (`num_gpu=0`) over 14 real Minder messages, scoring intent
`kind` accuracy and scenario field extraction:

| Model | kind | latency (CPU)¹ | scenario fields |
| --- | --- | --- | --- |
| **qwen2.5:7b** (chosen) | **14/14** | ~10–19s | clean (car→car, 9pm→21:00, water_level) |
| gemma3:4b | 14/14 | ~8s | good (car→"vehicle", time not normalized); fits 4GB GPU |
| qwen2.5:3b | 13/14 | ~8s | clean; fits 4GB GPU; only miss = weather→query |
| gemma4:e2b | 13/14 | ~7s | good |
| qwen3.5:4b / qwen3.5:9b | 14/14 | 60s / 97s | accurate but **thinking → unusably slow** |
| qwen3:1.7b / phi4-mini | 12/14 | ~7–9s | weaker |
| `llama3.2:3b` (old default) | 12/14 | — | **broken: `dev=None` on all 3 scenarios** |

¹ CPU/warm; ≤4B models run ~1–3s on GPU. Accuracy is device-independent.

**Decision: `qwen2.5:7b` on CPU** (`MINDER_ROUTER_MODEL`, `num_gpu=0`).
Rationale: perfect `kind` accuracy and the cleanest, already-normalized field
extraction (which matters most because the output feeds deterministic
execution); runs entirely in RAM so it never touches the GPU (the VLM is also on
CPU; the GPU stays free). The cost is latency — **~12–19s per query warm on
CPU** (verified on "is someone at the main door" → `query/person/[Main-Door]`,
and the full "outside / who's at the gate / any cameras" spread), on top of
execution. That is acceptable for a deliberate query and is being evaluated in
practice.

**Documented fast fallback:** `qwen2.5:3b` on the GPU (~1–2s, 13/14, the lone
weather miss covered by the keyword pre-check) if the CPU latency proves too
slow. Swappable via `MINDER_ROUTER_MODEL` + `MINDER_ROUTER_NUM_GPU` with no code
change. The old `llama3.2:3b` is **not** viable for routing (can't extract
scenario devices).

The current `llama3.2:3b` stays as `MINDER_REASONING_MODEL` for any remaining
agent work and as the keyword-`classify()` fallback substrate.

## Architecture

```
user message
   │
   ▼
pre_input gate (optional) ──► reject off-topic / unsafe before any work
   │
   ▼
minder-router  (reasoning agent, output_schema, NO tools)
   │   emits a validated Plan (the frozen intent contract)
   ▼
post_output / schema validation ──► auto-correct fields, retry ×2, else fallback
   │
   ▼
deterministic dispatch by Plan.kind
   ├─ query        → resolve cameras → YOLO (object) / VLM (open scene) → phrase reply
   ├─ device_now   → resolve device  → HA control → phrase reply
   ├─ scenario     → build a rule → HITL confirm → persist to rules.json (poller enforces)
   ├─ weather      → get-weather MCP tool → relay summary
   ├─ setup        → onboarding/config op
   └─ chat         → conversational reply
```

Keyword `classify()` is retained **only as a fallback** when the router is
unavailable (serve down) — it never re-becomes the primary path.

### The Plan schema (router `output_schema`)

```yaml
output_schema:
  type: object
  properties:
    kind:
      type: string
      enum: [query, device_now, scenario, weather, setup, camera_list, device_list, chat]
    subject:                      # for query: what to look for
      type: string                # "person" | "vehicle" | "animal" | "open" (open-scene)
    cameras:                      # resolved camera names, or ["all"] / outdoor group
      type: array
      items: { type: string }
    condition:                    # natural-language condition for open-scene VLM
      type: string
    device:                       # for device_now / scenario action
      type: string
    operation:                    # "on" | "off" | "toggle"
      type: string
    trigger:                      # for scenario (persistent rule)
      type: object
      properties:
        type:    { type: string } # "vision" | "sensor" | "time"
        object:  { type: string } # e.g. "car"
        camera:  { type: string }
        sensor:  { type: string } # e.g. water-level entity
        comparator: { type: string }
        value:   { type: string }
        at:      { type: string } # time rules
    reply_hint:                   # short human phrasing the model suggests (advisory)
      type: string
  required: [kind]
```

### Camera resolution & zones (outdoor tagging)

`qwen2.5:7b` resolves group words well ("outside" → the outdoor cameras,
"the gate" → "Main Gate - 2", "any cameras" → `["all"]`), but in the benchmark
it inferred *which* cameras are outdoor purely from their **names**
(Bedroom-Deck/Office guessed indoor). That is clever but fragile — a camera
named "Cam-3" gives the model nothing to go on.

So we make grouping **deterministic, not name-guessed**: each camera carries an
explicit `zone` in `cameras.json`.

```jsonc
// cameras.json entry
{ "name": "Main-Door", "ip": "192.168.0.101", "tier": "frigate",
  "zone": "outdoor" }          // "outdoor" | "indoor"  (default "outdoor" for security cams)
```

- The router **context** lists each camera with its zone, so the model resolves
  "outside"/"inside" against real data, not name vibes.
- The deterministic dispatcher **re-resolves** group words against the `zone`
  tag regardless of what the model returned — the tag is authoritative; the
  model's camera list is a hint that the backend validates/expands. ("outside"
  → every camera with `zone: outdoor`; an unknown camera name → dropped.)
- Zone is set at discovery (default `outdoor` for security cameras) and editable
  in the dashboard Cameras tab. Backward-compatible: a missing `zone` defaults
  to `outdoor`.

Two of the user's examples compile to:

```
"switch on the gate light when you see a car at the gate"
→ { kind: scenario,
    trigger: { type: vision, object: car, camera: "Main Gate - 2" },
    device: "gate light", operation: on }

"switch off the pump when water level is reached"
→ { kind: scenario,
    trigger: { type: sensor, sensor: "<water_level entity>", comparator: ">=", value: "<level>" },
    device: "pump", operation: off }

"do you see anyone outside?"
→ { kind: query, subject: person, cameras: [<outdoor cams>] }
```

### Scenario authoring vs. immediate execution

`scenario` plans are **not executed once** — they are **authored as durable
rules**. The LLM authors the rule a single time; Minder's existing deterministic
engine (`rules.json` + the Frigate event / sensor poller) enforces it forever.
This matches SwarmKit's "growth through human-approved authoring" pillar and
keeps the 24/7 loop LLM-free. Because a `scenario` actuates hardware, it passes
the **HITL confirm gate** before persisting (Minder's "no autonomous actions
without confirmation" rule) — the user sees "Create rule: when a car is at Main
Gate, turn the gate light on?" and approves.

Sensor-condition triggers (`water level`) are new to Minder's rule engine; v1 of
this note covers vision + time triggers (already supported by the poller) and
**defers** sensor-condition rules to a follow-up (see Open Questions).

## API shape

```
examples/minder/workspace/topologies/minder-router.yaml   # new: reasoning agent, output_schema, no tools, no skills
examples/minder/webapp/minder_ops.py
    async def route(text, source, sender) -> Plan          # runs minder-router, validates the plan
    async def handle_message(...)                          # calls route(); dispatches by Plan.kind; falls back to classify()
    def _dispatch_query(plan) / _dispatch_device(plan) / _author_scenario(plan) ...  # deterministic executors
    def _resolve_cameras(plan) -> list[str]                # zone-aware: re-resolves "outside"/["all"] against cameras.json zone tags

# env (docker-compose.yml / .env.example)
MINDER_ROUTER_MODEL=qwen2.5:7b      # the router/intent model (chosen)
MINDER_ROUTER_NUM_GPU=0             # 0 = CPU (default); set >0 to put the router on the GPU
# cameras.json gains a per-camera "zone": "outdoor" | "indoor" (default "outdoor"),
# set at discovery, editable in the dashboard Cameras tab.
```

The router topology declares **no skills and no tools** (so no arg-poisoning) and
relies on `output_schema` (Tier 0/1) for a clean Plan. `handle_message` becomes:
`plan = await route(text)` → validate → `dispatch(plan)`; on router failure or
low confidence, fall back to the current keyword path.

## Test plan

- **Unit:** `Plan` validation (schema conformance, enum bounds); camera/device
  resolution (`_match_camera_name` over groups like "outside"); scenario→rule
  translation; fallback-to-`classify()` when the router errors.
- **Integration (live `swarmkit run` + Ollama):** run `minder-router` on a fixture
  set of messages, assert the emitted `kind`/entities. The router is a pure
  classification+extraction task — measure accuracy over ~20 phrased variants.
- **Full pipeline:** `handle_message` end-to-end for query / device / scenario /
  weather, asserting the deterministic execution and reply.
- **Test data:** a `router_cases.json` of `{message → expected plan}` pairs,
  including the three failing transcripts.

## Demo plan

Terminal/Telegram transcript proving the exact failures are fixed:

```
"do you see anyone outside?"                    → checks outdoor cameras, reports per-camera person verdict
"do you see any person in any of the cameras"   → checks all cameras, reports where a person is/ isn't
"switch on the gate light when a car's at the gate" → "Create rule …?" → approve → rule persisted; poller fires it
```

Plus a `router_cases.json` accuracy summary (N/N correct `kind`) attached to the PR.

## Resolved

- **Router reliability (was open question #1).** Benchmarked (see Model). A small
  instruct model handles multi-clause scenario authoring cleanly — `qwen2.5:7b`
  scored 14/14 with correct trigger+device extraction on "gate light when a car's
  at the gate" and the sensor/time cases. `post_output` validation + the HITL
  confirm gate remain as backstops for harder multi-clause phrasings.
- **Camera grouping (was a fragility note).** Resolved via explicit `zone` tags
  in `cameras.json` + deterministic re-resolution in the dispatcher (see Camera
  resolution & zones) — no longer reliant on the model guessing from names.

## Open questions

1. **Sensor-condition triggers** ("water level reached") require a new trigger
   type in Minder's rule engine + the poller (today: vision + time). Deferred to a
   follow-up; the router schema already reserves `trigger.type: sensor`.
2. **Framework-level synthesis.** This pattern ("intent router → validated frozen
   plan → deterministic dispatch") is composed from `design/details/*` pages but
   is not unified anywhere, and `design/SwarmKit-Design-v0.6.md` doesn't mention
   it. Worth a SwarmKit design note (and a §-level entry) so it's a first-class,
   named pattern — not just a Minder idiom. Out of scope for this example PR.
3. **CPU router latency.** `qwen2.5:7b` is ~12–19s/query warm on CPU, ~15–25s
   end-to-end with execution. Being evaluated in practice; the GPU `qwen2.5:3b`
   fallback (~1–2s) is one env var away if it feels too slow.
