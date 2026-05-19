---
title: Structured inter-agent communication — output schemas for workers
description: Replace prose between agents with structured JSON output. Research-backed (CodeAgents, OPTIMA): 55-87% token reduction + 3-36% accuracy improvement.
tags: [compiler, performance, quality, tokens]
status: complete
---

# Structured inter-agent communication

## Problem

Agents currently communicate via free-text prose. This causes:

1. **Token waste** — connecting words, transitions, narrative structure
   add no information value between agents (only useful for humans)
2. **Fabrication opportunity** — prose lets the model weave unsourced
   claims into fluent narrative. Harder to audit than structured fields.
3. **Semantic compression loss** — when one model's internal state gets
   downsampled to natural language, information is irreversibly lost
   (arXiv:2506.02739)
4. **Intention drift** — continued NL interaction between agents
   progressively deviates from original consensus
5. **Re-parsing overhead** — coordinator must re-read full prose to
   extract the 3-4 relevant facts

## Research evidence

| Paper | Venue | Approach | Token reduction | Accuracy |
|---|---|---|---|---|
| CodeAgents | arXiv 2025 | YAML roles + pseudocode output | 55-87% in, 41-70% out | +3-36% |
| OPTIMA | ACL 2025 | RL-trained conciseness | 90%+ | +2.8x |
| State Delta | EMNLP 2025 | NL + state transition diffs | ~40% | SOTA reasoning |

Key finding from CodeAgents: **structural reformatting alone** (no
training, no model changes) eliminates redundancy that was actively
hurting accuracy. This is a prompting-only improvement — directly
applicable to SwarmKit's API-based architecture.

## Design

### Four structured communication surfaces

The framework has four places where information flows between
components. ALL should be structured, not prose:

| Surface | Current | Structured |
|---|---|---|
| Worker → coordinator | Prose findings | JSON output_schema |
| MCP tool → agent | Raw text | Provenance envelope |
| Compiler → agent (checkpoints) | English instructions | JSON action spec |
| Agent → compiler (scope/plan) | Already structured | Already structured |

Surface 4 is already done (create-scope, create-task-plan are
structured tool calls). Surfaces 1-3 are the implementation work.

### Three layers working together

```
┌─────────────────────────────────────────────────────┐
│  Layer 1: MCP Runtime Proxy                          │
│  Every tool call goes through runtime wrapper.       │
│  Adds provenance metadata to every response.         │
│  Schema enforcement, observability, caching.         │
└─────────────────────────────┬───────────────────────┘
                              │ enriched responses
                              ▼
┌─────────────────────────────────────────────────────┐
│  Layer 2: Default Output Schema                      │
│  All workers produce structured JSON by default.     │
│  Sources auto-populated from Layer 1 metadata.       │
│  Opt-out for creative/artifact-producing agents.     │
└─────────────────────────────┬───────────────────────┘
                              │ structured findings
                              ▼
┌─────────────────────────────────────────────────────┐
│  Layer 3: Validation (optional Rynko Flow)           │
│  Deterministic schema check (always, free).          │
│  Rynko Flow gate (opt-in, for business validation).  │
└─────────────────────────────────────────────────────┘
```

### Layer 1: MCP Runtime Proxy (now) → MCP Gateway (later)

This layer has two phases:

**Phase A (now):** Provenance envelope on `MCPClientManager.call_tool`.
Lightweight — 20 lines added to the existing call path. Every tool
call already goes through this method. We just wrap the response.

**Phase B (M10/M11):** Full `swarmkit mcp-gateway` as described in
`design/details/mcp-discovery-pattern.md`. Single process wrapping
all workspace servers. Discovery-first tool surface. Namespace routing.

Phase A's `ToolMetadata` shape becomes the contract Phase B respects.
Clean migration: same provenance format, different producer.

#### Phase A: Provenance envelope (implement now)

`MCPClientManager.call_tool` is already the single point where
ALL MCP calls pass through. It already does governance gating.
We extend it to wrap every tool response with provenance metadata:

```python
# MCPClientManager.call_tool (enhanced)
async def call_tool(self, server_id, tool_name, arguments):
    # Existing: governance gate
    decision = await self.governance.evaluate_action(...)

    # Existing: execute tool
    start = time.time()
    result = await session.call_tool(tool_name, arguments)
    elapsed = int((time.time() - start) * 1000)

    # NEW: wrap response with provenance envelope
    return ToolResponse(
        data=result.content,
        metadata=ToolMetadata(
            source=f"{server_id}:{tool_name}",
            args=arguments,
            timestamp=datetime.now(tz=UTC).isoformat(),
            duration_ms=elapsed,
            server_id=server_id,
        )
    )
```

#### Phase B: Full MCP Gateway (M10/M11)

See `design/details/mcp-discovery-pattern.md` for the full design.
The gateway adds:

- Single process (not 9 separate child processes)
- Discovery-first: `discover_capabilities` + `execute_tool`
- Namespace routing: `gateway.github.get_pr`, `gateway.cdt.search`
- Response schema enforcement at gateway level
- Corsair-inspired permission tiers with per-tool overrides
- Connection pooling and request coalescing

The provenance envelope from Phase A is preserved — the gateway
produces the same `ToolMetadata` shape, just from a centralized
process instead of the client manager.

**Benefits (both phases):**
- Citation becomes automatic (source is in the metadata)
- Observability for free (every call logged with timing)
- Schema enforcement point (validate response shape)
- Caching decisions informed (same args → same response)
- Works for community servers without modification

### Layer 2: Default Output Schema

All workers produce structured JSON by default. The platform
provides a base schema that all workers inherit:

```yaml
# Platform default — applied to all role=worker agents
# unless archetype sets output_schema: null
output_schema:
  type: object
  required: [findings]
  properties:
    findings:
      type: array
      items:
        type: object
        required: [fact, source]
        properties:
          fact:
            type: string
            description: One atomic claim or data point
          source:
            type: string
            description: Auto-populated from tool metadata
          confidence:
            type: string
            enum: [observed, inferred]
    not_found:
      type: array
      items:
        type: string
        description: What was searched for but not found
    raw_data:
      type: object
      description: Key structured data (IDs, configs, tables)
      additionalProperties: true
```

**Default-on, opt-out for creators:**
- `role: worker` → output_schema applied automatically
- `role: leader` / `role: root` → prose (human-facing)
- Archetype sets `output_schema: null` → prose (document-writer)

**The `source` field is auto-populated** from Layer 1 metadata.
The agent doesn't need to manually cite — the runtime knows which
tool call produced which data point because the proxy tracked it.

### Layer 3: Validation

Two tiers:

**Tier 1 (always, free):** JSON Schema validation against
`output_schema`. Built into the compiler. Invalid → retry with
schema feedback. No external service needed.

**Tier 2 (opt-in, Rynko Flow):** for workspaces that need business
validation beyond schema shape. Wired as a governance decision
skill with `implementation.type: mcp_tool` pointing at Rynko Flow.
Example: "every finding about a pipeline must reference a real
pipeline ID from the CDT" — that's domain validation, not schema.

```yaml
# workspace.yaml — opt-in Rynko validation
governance:
  decision_skills:
    - id: rynko-output-validator
      trigger: post_output
      config:
        gate_id: "sterling-findings-gate"
```

### How it works end-to-end

```
Agent calls tool via MCP
  → Layer 1 proxy wraps response with metadata (source, timing)
  → Agent sees enriched data
  
Agent produces output
  → Layer 2 default schema enforces structured JSON
  → source fields auto-filled from tool call metadata
  → Tier 1 validation: schema check (deterministic, free)
  
If Rynko configured:
  → Tier 2 validation: Rynko gate checks business rules
  
Output reaches coordinator
  → Structured findings with provenance
  → No summarizer needed
  → Cross-validation is trivial (compare source fields)
```

### Authoring skill integration

When `swarmkit author archetype` creates a new worker:
- Auto-includes the default `output_schema` (unless creative role)
- Suggests domain-specific `raw_data` fields based on the tools

When `swarmkit author skill` creates a new MCP tool skill:
- The tool automatically gets the runtime proxy (no config needed)
- The authoring skill can suggest response schemas for the MCP
  server itself (guidance, not enforcement — community servers
  won't follow it, but authored ones will)

### Compiler behavior

When an archetype has `output_schema`:

1. **Structured output mode** — the compiler requests JSON mode from
   the model provider (most providers support this: OpenAI, Anthropic,
   DeepSeek, etc.)
2. **Schema validation** — response validated against the schema.
   Invalid responses trigger one retry with the schema as feedback.
3. **No summarization** — skip the `_summarize_result()` LLM call.
   The structured output is already concise.
4. **Direct to coordinator** — coordinator's checkpoint shows
   structured findings, not prose summaries.

### What the coordinator sees at checkpoint

```
TASK PLAN STATUS:

completed: jira-research (jira-researcher) — 12.3s, 6 tool calls
  FINDINGS:
  - RT-727 requires return processing for replacement orders [get-jira-issue(RT-727)]
  - Linked to RT-726 (cancellation of replacements) [get-jira-issue(RT-727).links]
  - Gopu comment 2026-04-29: RTO sub-scenario needed [get-jira-issue(RT-727).comments]
  - SAP must accept allocation feed with PaymentDetailsList [get-jira-issue(RT-727).description]
  NOT FOUND:
  - No confluence pages specifically about RT-727 return flow
  - No attachments on the ticket
```

vs current:
```
completed: jira-research (jira-researcher) — 12.3s, 6 tool calls
  - RT-727 is a CAI ticket about return of replacement orders
  - The ticket has 4 acceptance criteria related to SAP feeds
  - Related ticket RT-726 handles cancellation
  - Gopu commented about RTO scenario
```

The structured version has **sources on every claim** and explicitly
reports what wasn't found. The current version has no provenance.

### Which agents get output_schema?

**Workers that research/retrieve:** jira-researcher, config-analyst,
docs-researcher, log-analyst, sterling-developer. These produce
factual findings that should be atomic and sourced.

**Workers that create:** document-writer. Does NOT get output_schema
— its output IS the final artifact (prose document).

**Coordinator:** does NOT get output_schema — its synthesis is the
human-readable output.

### Interaction with governance

With `output_schema`, grounding verification becomes **Tier 1
(deterministic)** instead of Tier 3 (LLM judge):

```python
# Deterministic check — no LLM needed
for finding in output["findings"]:
    if not finding.get("source"):
        flag("unsourced claim", finding["fact"])
```

This replaces the `grounding-verifier` LLM decision skill for agents
with output_schema. The governance layer checks the schema constraint
directly — every finding must have a source field. Fabrication without
a source becomes structurally impossible.

### Token savings estimate

Typical worker output today: ~2000 tokens (prose)
Same information as structured JSON: ~400 tokens

**5x reduction in inter-agent transfer tokens.** Plus elimination
of the summarizer LLM call (~500 tokens per task).

For a 5-task plan:
- Current: 5 × 2000 (output) + 5 × 500 (summarizer) = 12,500 tokens
- Structured: 5 × 400 (output) + 0 (no summarizer) = 2,000 tokens
- **Savings: 84%**

### Backward compatibility

- Archetypes WITHOUT `output_schema` work exactly as today (prose)
- `output_schema` is optional — opt-in per archetype
- Coordinator synthesis path unchanged (reads structured OR prose)
- Existing governance decision skills still fire (but grounding
  becomes deterministic for structured agents)

## Schema changes

### archetype.schema.json

Add `output_schema` to archetype defaults:

```json
"defaults": {
  "properties": {
    "output_schema": {
      "type": "object",
      "description": "JSON Schema for structured inter-agent output. When set, the agent produces JSON instead of prose, and structured output mode is requested from the model provider."
    }
  }
}
```

### Skill schema — no changes

Skills already have `outputs` schema for decision skills. Worker
output_schema is separate — it's on the archetype, not the skill.

## Implementation plan

### Wave 1: Provenance + structured output ✓ (v1.2.26–v1.2.29)

#### PR 1: MCP provenance envelope ✓ (v1.2.27, PR #223)
- `ToolMetadata` dataclass: source, args, timestamp, duration_ms, server_id
- `ToolResponse` dataclass: data + metadata
- Extend `MCPClientManager.call_tool` to wrap responses
- Skill executor unpacks the envelope, appends `[source: ...]` tag
- Phase A of the gateway path (see `mcp-discovery-pattern.md`)

#### PR 2: Default output_schema for workers ✓ (v1.2.26, PR #222)
- `output_schema` on archetype schema + topology agents (object|null)
- Platform default schema applied to all `role: worker` agents
- Compiler: system prompt injection, JSON mode via `response_format`
- `response_format` on CompletionRequest, wired through all providers
- Schema validation + retry on invalid. Summarizer bypass for JSON.

#### PR 3: Auto-populate source from tool metadata ✓ (v1.2.28, PR #224)
- Runtime scans messages for `[source: ...]` provenance tags
- Validates finding sources against known tool calls
- Auto-fills empty sources when unambiguous (single tool call)

#### PR 4: Skip summarizer (landed in PR 2)
- Structured JSON with `findings` array → extract directly, skip LLM

#### PR 4.5: Structured checkpoint instructions ✓ (v1.2.29, PR #225)
- Checkpoint prompts replaced with JSON action specs:
  `{phase, plan_status, required_actions, scope_status}`
- All 4 structured communication surfaces now complete

### Wave 2: Governance integration ✓ (v1.2.30–v1.2.31)

#### PR 5: Deterministic grounding (Tier 1) ✓ (v1.2.30, PR #227)
- `check_grounding()` — deterministic source check, no LLM needed
- Every finding must have non-empty `source` field
- Audit event `grounding.checked` with `deterministic: true`
- Replaces LLM-based grounding-verifier for structured agents

#### PR 6: MCP-backed decision skills ✓ (v1.2.31, PR #228)
- `mcp_manager` threaded through governance → decision evaluator
- Any MCP server can be a governance decision skill (not just Rynko)
- `_parse_result` strips provenance tags from MCP responses
- Reference skill YAML in `docs/examples/rynko-output-validator.yaml`

### Wave 3: Sterling + authoring + gate-validator ✓ (v1.2.32, PRs #230–#231)

#### PR 7+8: Sterling structured researchers + authoring ✓ (v1.2.32, PR #230)
- Sterling document-writer opts out with `output_schema: null`
- All research workers get structured output by default (no changes needed)
- Authoring prompt explains output_schema, shows both patterns (research vs creator)

#### Gate-validator MCP server ✓ (v1.2.32, PR #231)
- New MCP server: `python -m swarmkit_runtime.gate_validator`
- Drop JSON Schema files into `gates/` → validation gates for agent output
- `list_gates` + `validate_gate` tools, returns decision skill result format
- Sterling workspace wired: 3 domain-specific gate schemas (findings, code, config)
- LLM-based grounding-verifier disabled in favour of deterministic gate validation
- Any MCP-backed decision skill works (not just Rynko) — generic infrastructure

### Future: Full MCP Gateway (M10/M11)

#### PR 9+: swarmkit mcp-gateway
- Single process wrapping all workspace servers
- Discovery-first: `discover_capabilities` + `execute_tool`
- Namespace routing: `gateway.{server}.{tool}`
- Response schema enforcement at gateway level
- Corsair-inspired architecture (see `mcp-discovery-pattern.md`)
- Replaces Phase A proxy with Phase B gateway
- Same `ToolMetadata` contract, centralized producer

## Design decisions (resolved)

1. **Default-on, not opt-in.** All workers get structured output
   by default. Creators opt out with `output_schema: null`.

2. **MCP runtime proxy is universal.** Every MCP call goes through
   the provenance wrapper — community servers, authored servers,
   remote servers. No server-side changes needed.

3. **Rynko is opt-in only.** Tier 1 (schema check) is always free.
   Rynko business validation is a governance decision skill that
   workspaces explicitly wire.

4. **Source auto-population.** The runtime tracks which tool call
   produced which data. Agents don't manually cite — provenance is
   infrastructure, not prompt-dependent.

5. **Authoring skill generates this by default.** New archetypes
   come with output_schema. New MCP skills authored by the platform
   follow a standard response structure.

## Open questions

1. **Raw data size:** should `raw_data` have a token budget? Large
   XML configs could defeat the purpose. Probably cap at 2000 tokens
   and reference disk path for full data.

2. **Model JSON mode support:** DeepSeek V4 Flash handles JSON well.
   Kimi K2.5 needs testing. Fallback: if model doesn't support
   `response_format: json`, prompt-instruct instead.

3. **Coordinator consumption:** does Kimi K2.5 (coordinator) perform
   better reading JSON vs prose? CodeAgents says yes, but needs
   validation with our specific model stack.

4. **Incremental rollout:** should we flip all Sterling workers at
   once or one-by-one to measure impact?
