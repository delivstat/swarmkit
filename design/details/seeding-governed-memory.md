# Putting a fact into governed memory

**Status:** proposed — design only.

## Goal

Let a human — or an application — write a fact into governed memory, through the same governed path
an agent writes through.

## Why there is a gap

`swarmkit memory` has `search`, `get`, `quarantine`, `resolve`. The HTTP surface has three GETs and a
resolve POST. **Neither can write.** The only write path is `GovernedMemoryStore.write()`, reached
from `governed_memory_post_output` — the persistence-skill hook that runs after an agent's turn.

So a fact enters governed memory only when **an agent chooses to emit it**, in the
`{"memories": [...]}` shape `parse_candidates` expects, during a run, while carrying the
`governed-memory` skill.

That is the wrong shape for the facts most worth keeping. The durable ones are discovered
incidentally — *"sn8 has no per-screen label bundle"*, *"resource wiring is not a file"* — by an agent
doing something else, with no reason to format them as memory candidates. They are established at
real cost, and there is no way to put them in afterwards.

It also makes the store's value untestable. "Do curated facts stop the next ticket paying again?" is
a question you cannot even set up, because you cannot seed the store to ask it.

## The command

```
swarmkit memory add <subject> <attribute> <value> [-w WORKSPACE]
    --type semantic|profile|procedural|episodic|working   (default: semantic)
    --confidence 0.0–1.0                                  (default: 1.0)
    --source TEXT                                         (default: the OS user)
    --from-file PATH                                      (bulk; mutually exclusive with the args)
```

### It goes through `write()`, and says what happened

The point is not to insert a row. `write()` reconciles the candidate against current memory and
returns one of `new` / `update` / `reinforce` / `refine` / `contradict`. A human add inherits all of
it, including contradiction handling, for free.

So the command reports the **op**, never a bare "added":

```
$ swarmkit memory add sn8 carton-count-source "Comes from the TASK LIST, not YFS_SHIPMENT_CONTAINER."
new       sn8/carton-count-source
```

```
$ swarmkit memory add sn8 carton-count-source "Comes from YFS_SHIPMENT_CONTAINER."
contradict  sn8/carton-count-source — NOT written; quarantined for review
  the trusted memory is unchanged. Resolve with: swarmkit memory resolve <id>
```

**A `contradict` must not read as success.** The trusted memory is deliberately left untouched and
the candidate is quarantined — so a command that printed "added" would be stating the opposite of
what happened, which is the exact defect shape this codebase has spent a week removing.

Proposed exit codes: `0` for `new` / `update` / `reinforce` / `refine`, non-zero for `contradict`, so
a script seeding facts can branch on it rather than parsing stdout.

### `--from-file` takes the agent's own format

```json
{"memories": [{"subject": "sn8", "attribute": "carton-count-source", "value": "…"}]}
```

Deliberately the same shape `parse_candidates` reads, for three reasons: seeding an estate one fact
at a time is unusable; an agent's output can be replayed into the store when it emitted candidates
the run did not write; and one format means one thing to document and one parser to keep correct.

### `source` identifies the human

The hook stamps `source=agent_id` when a candidate does not name one. A CLI add has no agent, and
"who asserted this" is the question that gets asked later — so it defaults to the OS user rather
than being left null, and is overridable for an application writing on someone's behalf.

## Refuse to write where nothing will read

`_create_governed_memory_store` returns `None` unless the workspace declares the `governed-memory`
skill, so there is no store to write to. **Refuse, naming the missing skill** rather than failing
obscurely.

Second, softer check: if no agent binds `memory-reader` at `pre_input`, the fact will be stored and
never injected. **Warn, do not refuse** — seeding before wiring is a legitimate order of work — but
say so, because the alternative is a store filling up with facts that reach no run.

Both diagnose the silent chain that makes this feature necessary: the store is `None`, or the agent
lacks the grant, or the model never emits candidates, and **every one of those failures looks
identical to "nothing to remember"**.

## Writes are not audited today

`GovernedMemoryStore` holds no governance handle and emits no `AuditEvent` — not for a human write,
and not for an agent's. Governed memory feeds agent prompts, so a fact entering it changes what
every later run believes, from an actor nobody recorded.

`GovernedMemoryStore` should take an optional governance handle and audit **every** write —
`memory.written` with the op, key, source and confidence. Doing it in the store rather than at the
CLI boundary means the agent path is covered by the same line, which is the whole reason to put it
there. `_create_governed_memory_store` already constructs the store with a reconciler, so the
governance handle is a symmetric addition.

## `POST /memory`

Same fields, same reconcile, same response shape (`{op, key, changed}`). An application owning its
own sequencing — the WMS driver — should be able to record what a resolution established without
shelling out to the CLI. Small, and it keeps the CLI and HTTP surfaces from diverging the way the
run surfaces did.

## Non-goals

- **No delete, and no overwrite that bypasses reconcile.** Correcting a fact means adding the
  corrected value and letting the store judge it — that is what the governed write path is for, and
  a back door around it defeats the feature.
- Not a memory editor or a browsing UI.
- Not changing reconcile semantics, quarantine, or `resolve`.
- Not making `governed-memory` present by default in a workspace.

## Test plan

- A first write of a key reports `new`; an identical repeat reports `reinforce` and does not change
  the value; a changed value reports `update` or `refine`.
- **A contradicting write leaves the trusted memory unchanged, quarantines the candidate, and exits
  non-zero** — the assertion that stops "added" from being printed over a rejection.
- The quarantined item appears in `swarmkit memory quarantine` and is resolvable by `resolve`.
- `--from-file` writes every candidate and reports the op counts; a malformed file fails without a
  partial write.
- `source` defaults to the OS user and is overridable; `confidence` and `type` reach the row.
- A workspace with no `governed-memory` skill refuses, naming the skill.
- A workspace with no `memory-reader` binding warns and still writes.
- Every write emits `memory.written` — asserted for **both** the CLI path and the agent hook.
- `POST /memory` and `swarmkit memory add` produce identical outcomes for identical input.
- End to end: a seeded fact is retrieved by `memory_pre_input` and appears in an agent's rendered
  input — the only assertion that proves seeding is worth anything.

## Demo plan

Seed the two facts the WMS developer stage established, then show them reaching a run:

```
$ swarmkit memory add sn8 label-bundles "sn8 has no per-screen label bundle; labels are global."
new  sn8/label-bundles
$ swarmkit memory search "label bundle"
sn8/label-bundles  (semantic, 1.00, source srijith)  sn8 has no per-screen label bundle…
$ swarmkit run ws wms-design -i "…" --verbose | grep "memory context injected"
```

And the contradiction path, showing the trusted value surviving a wrong add.

## Open questions for review

1. **Is non-zero exit on `contradict` right?** It is not an error — the store did exactly what it is
   designed to do. But a seeding script that cannot distinguish "stored" from "rejected and
   quarantined" is the failure this note exists to prevent.
2. **Should `source` carry an identity rather than an OS username?** Serve has auth; the CLI does
   not. `srijith` is honest locally and meaningless in a fleet.
3. **Does auditing agent writes belong in this note or its own?** It is one line in the same place,
   but it changes the audit volume of every existing memory-writing run.
4. **Should `add` be able to seed a quarantine directly** — "these two facts conflict, a human should
   choose" — or is quarantine strictly an outcome of reconciliation?
