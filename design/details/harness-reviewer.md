# Harness reviewer (investigative, non-coding harness)

Parent: `design/details/sdlc-pipeline-example.md` (capability 4 of 5). A reusable pattern, not
SDLC-specific: a harness executor used for **investigative review** rather than authoring.

SwarmKit already has harness executors that *write* — a session-holding, diff-producing harness
(`developer`, on claude-code / opencode). This note establishes the mirror-image use: a harness that
*reads and investigates* — it opens the repo + knowledge bases, cross-checks an artifact against
reality, and returns findings. It is layer 3 of the gate funnel (`gate-funnel`), the step where a
text-only judge is not enough.

## Goal

Make "an independent reviewer that goes and looks" a reusable archetype shape: a **read-scoped
harness executor** that produces structured findings on an artifact, distinct from both a stateless
LLM judge (which only sees the text it is handed) and a coding harness (which mutates a repo).

## Non-goals

- **Not authoring.** The reviewer never produces a diff or writes to a repo; its output is findings.
  A different executor kind is not needed — same `harness` executor, read-only scope, findings schema.
- **Not the gate composition.** How findings gate advancement (attach vs route-back-at-severity) is
  `gate-funnel`; this note defines the reviewer that *produces* the findings.
- **Not the judge.** The layer-2 LLM-as-judge (`artifact-judge`) is a stateless decision skill;
  the reviewer is a session-holding investigator. They are complementary, not substitutes.

## Where it lives

Two reusable archetypes in the SDLC library (`architect-reviewer`, `security-consultant`), plus the
convention that any reviewer is a `harness` executor with **read-only** resource scope and a
**findings output schema**. No new executor kind — it composes the existing executor abstraction
(`design/details/executor-abstraction.md`) with a read-scoped grant and a structured output.

## API shape

### Reviewer archetype (harness, read-scoped)

```yaml
apiVersion: swarmkit/v1
kind: Archetype
id: architect-reviewer
executor: { kind: harness, adapter: claude-code }   # config-selected; opencode equally valid
scope:
  read:  [app:oms, app:web, app:mobile, kb:architecture, kb:consolidated-design]
  write: []                                          # investigative only — no repo writes
output_schema: schemas/review-findings.json          # structured findings, not prose
```

The read scope is enforced structurally (same per-app IAM as the rest of the pipeline); an empty
write scope means the harness cannot mutate anything — it can run analysis, read code and KBs, but
its only product is findings.

### Findings schema

```yaml
findings:
  - id: F-1
    severity: high            # info | low | medium | high | critical
    location: "oms/api/order.py:214 vs consolidated-design §4.2"
    claim: "Design specifies idempotent order submit; code retries without an idempotency key."
    evidence: ["order.py:214", "consolidated-design §4.2"]
    recommendation: "Add an idempotency key to POST /orders per the contract."
```

Findings are **evidence-bearing** (they cite the code/KB locations checked), which is what makes a
harness review more than an opinion — and what `gate-funnel`'s `route_back_at` severity threshold
keys on.

### Investigation contract

The reviewer is prompted to *investigate*, not to rubber-stamp: open the artifact, then the repo and
KBs it references, verify claims against reality, and report discrepancies with evidence. Two named
reviewers ship:

- `architect-reviewer` — cross-checks the consolidated design against the actual code + integration
  points (does the design match what the code does; are the integration contracts honoured).
- `security-consultant` — hunts for gaps against the compliance/SAST/DAST KB (data-residency, authz,
  injection surfaces) that a design read alone would miss.

### Determinism boundary

The *investigation* is LLM/harness work (judgement); the **findings schema validation** is
deterministic (structured-output governance). So a malformed finding is caught mechanically before it
reaches the gate — the same validate-then-judge discipline as everywhere else.

## Eject

The reviewer ejects like any harness-executor node: a node invoking the harness adapter with a
read-only scope and a structured-output constraint. Its findings are node output. Nothing here needs
a bespoke ejection path beyond the existing executor eject.

## Test plan

- **Scope enforcement:** an `architect-reviewer` write attempt is refused (empty write scope); a read
  outside its granted apps/KBs is refused.
- **Findings schema:** valid findings parse; a finding missing `severity`/`evidence` is rejected
  (deterministic validation before the gate).
- **Planted-defect detection (integration):** `architect-reviewer` surfaces a seeded design↔code
  mismatch with the correct location + evidence; `security-consultant` surfaces a seeded compliance
  gap; a clean artifact yields no high/critical findings (low false-positive check).
- **Adapter swap:** the same reviewer runs on `claude-code` or `opencode` by config (executors-are-
  data), findings unchanged in shape.

## Demo plan

`just demo-harness-reviewer`: run `architect-reviewer` against a demo repo whose code diverges from
its design in one known place, and `security-consultant` against a repo with a planted data-residency
gap. Show the evidence-bearing findings and how a `high` finding would route back through the funnel
while `info` findings merely attach. Terminal transcript in the PR body.

## Schema-change checklist

Adds the review-findings output schema (a skill/archetype output contract) — follow
`docs/notes/schema-change-discipline.md`: canonical JSON Schema, Python + TS validators, fixtures. No
new executor kind; reuses the harness executor + read-only scope.
