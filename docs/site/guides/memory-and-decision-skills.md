# Memory and decision-skill bindings

Two subsystems that look like one, and one flag that used to mean two things. Both caused silent
failures — a curated fact that reached no agent, and a binding that was accepted and never
evaluated. This is what each actually does and how to turn it on.

## There are two memories

They are separate stores with different shapes, different write paths, and different purposes.
`swarmkit memory` addresses the first; the `memory-reader` skill historically read only the second.

| | governed memory | workspace memory |
| --- | --- | --- |
| shape | `{subject, attribute, value, type, confidence}` | `{topic, context, key_points, tags}` |
| written by | `governed-memory` skill, `swarmkit memory`, the curation flow | `memory-writer` skill, automatically after a run |
| reviewed | reconcile-on-write, quarantine on contradiction, human gate | no |
| decays | yes (confidence by recency) | no |
| stored in | the configured store (`storage.runtime`) | `.swarmkit/memory.json`, or GBrain |
| CLI | `swarmkit memory search \| get \| quarantine \| resolve` | — |
| UI | `/memory` | — |

**Governed memory is for facts you want an agent to treat as established** — a correction a human
made, a decision that should not be re-litigated. It is curated: a write is reconciled against what
is already there, a contradiction is quarantined rather than applied, and resolution is a human
action. That machinery is what makes a fact trustworthy enough to act on.

**Workspace memory is what a run remembered by itself.** Useful, unreviewed, and rewritten freely.

Since 1.168.0 the `memory-reader` binding reads **both**, and curated facts are rendered first —
they went through review; workspace memory is whatever a previous run happened to record.

## Turning governed memory on

Three things have to be true. Missing any one of them fails silently, which is what made this worth
a guide.

### 1. Declare the `governed-memory` skill in the workspace

This is what causes the store to be constructed at all.

```yaml
# workspace.yaml
skills:
  - governed-memory
```

### 2. Bind `memory-reader` at `pre_input`

This is what causes curated facts to be injected into an agent's input.

```yaml
# workspace.yaml
governance:
  decision_skills:
    - id: memory-reader
      trigger: pre_input
      required: false          # advisory — see below, and read that section
      config:
        governed_limit: 5      # how many curated facts to inject (default 5)
```

`required: false` is correct here. A memory read that can fail a run is worse than no memory. Note
that before **1.169.0** this silently discarded the binding — if you are on an earlier version, the
reader never runs, and `swarmkit memory search` showing your fact tells you nothing about whether an
agent can see it.

### 3. Grant `governed-memory` to an agent that should WRITE

Reading needs no grant beyond the binding. Writing does, and the skill carries `kb:write`:

```yaml
# archetypes/curator.yaml
defaults:
  skills:
    - governed-memory        # write access — grant deliberately
```

Do **not** grant this to make reading work. Reading comes from the `memory-reader` binding; granting
the write skill to get reads is how a curated store stops being curated.

### Confirming it works

The reader logs when it injects:

```
Memory context injected for agent=triage (user=None, query=enumerate the cartons...)
```

and the agent's input gains a delimited block:

```
<curated-memory>
Established facts for this workspace:
- sn8 · carton-count-source: Carton count comes from the TASK LIST, not Shipment/Containers.
</curated-memory>
```

If `swarmkit memory search` finds the fact and that line never appears, the binding is not reaching
the compiler — check your version and the `enabled`/`required` spelling below.

## Writing a curated fact

```bash
swarmkit memory search "carton count"          # what is already known
swarmkit memory get sn8 carton-count-source    # one fact, with its history
```

A write goes through reconciliation: a new value for an existing `(subject, attribute)` is compared
against the current one and classified — reinforce, refine, update, or **contradict**. A
contradiction is quarantined rather than applied, and `swarmkit memory resolve` is a human decision.
That is the point of the store; an agent cannot overwrite a reviewed fact by asserting louder.

## Decision-skill bindings: `enabled` and `required`

Two questions, two flags. They were one flag until 1.169.0, and collapsing them is what made an
advisory binding disappear.

```yaml
governance:
  decision_skills:
    - id: memory-reader
      trigger: pre_input
      enabled: true          # does it run at all?      (default true)
      required: false        # can a `fail` stop the run? (default true)
```

| | `enabled` | `required` |
| --- | --- | --- |
| asks | does this binding run? | can its verdict stop the run? |
| `false` means | the skill is not bound; nothing happens | the skill runs; a `fail` is logged, not fatal |
| set it in a topology to | switch off something inherited from the workspace | make an inherited gate advisory |

### The triggers

| trigger | fires | a `fail` from a required binding |
| --- | --- | --- |
| `pre_input` | before any LLM work | rejects the input; no tokens spent |
| `post_output` | after the agent answers | sends it back for revision, bounded by retries |
| `checkpoint` | between task batches | logged only |
| `pre_synthesis` | before a leader synthesises | logged only |

### Migrating from the old spelling

Before 1.169.0, a topology disabled an inherited binding with `required: false`, and a workspace
binding with `required: false` was discarded outright. If you have either:

```yaml
# was: disable an inherited binding
- id: grounding-verifier
  trigger: post_output
  required: false      # now means ADVISORY — it will run

# write instead:
- id: grounding-verifier
  trigger: post_output
  enabled: false       # off
```

The runtime warns when it sees the old shape on a topology override, naming the binding. It does not
reinterpret silently, because a gate that starts running when it used to be off is a real change.

## Why both of these failed quietly

Each is the same shape, and it is worth recognising:

- Governed memory had a full curation flow, a CLI, a UI page and a store — and no agent read path.
  The reader searched a different store and reported finding nothing, which is exactly what finding
  nothing looks like.
- An advisory binding was accepted by `swarmkit validate`, appeared in the resolved workspace, and
  was dropped at compile time with no message.

Neither produced an error. If you are configuring either and it appears to do nothing, the useful
question is not "is my YAML right" — it is "what does the runtime say it loaded". `swarmkit validate`
answers the first; the injection log line above answers the second.

## Related

- [Getting an image to a model](getting-an-image-to-a-model.md) — the same "it exists but nothing
  surfaces it" shape, one subsystem over
- `design/details/governed-memory.md` in the repo — the store, reconciliation and decay
- `docs/notes/reported-bugs.md` in the repo — bugs 21 and 22, the two failures above
