# Harness parity gaps

**What this note is for.** The declarative layer accepts a topology; the `model` path implements it;
the `harness` path quietly does not. Every bug in this class looks the same from outside: a control
you declared, that `swarmkit validate` accepted, that never ran — and a run that reports success.

Read this before assuming a topology behaves the same on `executor.kind: harness` as it did on
`model`. Add to it when you find another one.

Bugs outside the harness path go in the general ledger, [reported-bugs.md](reported-bugs.md).

## The invariant these all violate

`design/details/executor-abstraction.md`: a harness node is a **node**. Everything the declarative
layer says about a node — its governance gates, its output contract, its observability — is supposed
to hold regardless of which executor runs it. `executor` chooses the *mechanism*, not the *contract*.

Where that breaks, the failure is always in the unsafe direction: the control is absent, the run
looks fine, and nothing in the trace, the CLI or `validate` says otherwise. You find out from the
output being wrong, if you notice at all.

## Known gaps

| # | Gap | Status | Where |
| --- | --- | --- | --- |
| 1 | Tool-call outcomes discarded — a failed tool traced identically to a successful one | **fixed** (1.135.0) | `_harness_node.py`, all four adapters |
| 2 | Decision skills never run, including `required: true` | **fixed** (1.142.0) | `_compiler.py` `node_fn()` |
| 3 | `output_schema` ignored | **fixed** (1.143.0) | `_harness_node.py` (zero references) |
| 4 | `TaskSpec.context_files` is dead — set, never delivered | **fixed** (1.158.0) | executor plumbing |
| 5 | Images reach a model only via MCP `ImageContent`; relative paths resolve nowhere in the sandbox | **open** | harness sandbox + MCP gateway |
| 6 | `TaskSpec.mcp_tools` is dead, and the grant it would carry holds the wrong names | **fixed** (1.157.0) | executor plumbing + gateway naming |

### 6. `TaskSpec.mcp_tools` is dead, and holds the wrong names anyway (fixed, 1.157.0)

Reported as "the harness does not find tools that are in the gateway". Two problems stacked.

**It is never delivered.** `TaskSpec.mcp_tools` is assigned in exactly one place and read in none:

```
$ grep -rn "\.mcp_tools\|mcp_tools=" packages/runtime/src packages/runtime/tests | grep -v "def \|field("
packages/runtime/src/swarmkit_runtime/langgraph_compiler/_harness_node.py:189:        mcp_tools=mcp_tools,
```

Same shape as gap 4 — computed at the seam, dropped before the adapter. The only thing that
reaches `--allowedTools` is `config.allowed_tools`, which a human has to write by hand.

**The names would not match if it were.** `_task_spec` fills it with **skill ids** (`skill.id`, over
*all* the agent's skills). The gateway advertises `<server>__<tool>`, and Claude Code then exposes
that under its own MCP prefix as `mcp__swarmkit__<server>__<tool>` — three namespaces deep, none of
them the skill id. So an operator who sets `config.allowed_tools: search-wms-tables` — the obvious
thing, and the name the topology uses everywhere else — allowlists a tool that does not exist under
that name, and every real tool falls outside the grant.

The adapter comment says an unset grant means all tools, so the default path does work. It is
*setting* the grant that silently denies everything, which is the wrong way round: constraining the
agent should not be the thing that breaks it.

**Fixed** by deriving the allowlist from the gateway's own tool surface — the `GatewayTool.name` values
are already computed in `_wire_mcp_gateway` and recorded in the `executor.mcp_gateway` audit event —
rather than from skill ids, and deliver it through the adapter DSL so `config.allowed_tools` stops
being the only route. A translation table belongs in the adapter, since the prefix is harness-native
(`mcp__<server>__<tool>` is Claude Code's convention, not a universal one).

### 1. Tool-call outcomes discarded (fixed, 1.135.0)

A design agent described UI screens it had never seen, three runs running, because the image tool
was handed paths that resolved nowhere and returned nothing. The trace showed `view-screenshot ✓`
either way.

Three defects: no adapter except opencode mapped tool *outcomes* at all (claude-code reports them
out-of-band on a following `tool_result`, codex as an exit code, gemini on its own event);
`ExecToolCall.status` had no shared vocabulary, so opencode's raw `completed` meant nothing
downstream; and `ToolCall.result_length` was given the *argument* length, so a tool that returned
nothing still showed a healthy number because the path was long.

Fixed by normalizing once at the seam every adapter passes through — `""` / `ok` / `error`, where
`""` means *unreported* and is deliberately not `ok`. See `design/details/harness-tool-outcomes.md`.

### 2. Decision skills never run on a harness executor (fixed, 1.142.0)

`node_fn()` hands off to `run_harness_node()` with an early `return`, and every decision-skill gate
sits after it. `_ds_bindings` is computed for the agent and then discarded:

```python
if agent.executor.kind != "model":
    return await run_harness_node(...)   # <-- every gate below is unreachable

if _ds_bindings:                          # pre_input
if _ds_bindings:                          # post_output
```

`run_harness_node()` has no parameter to receive the bindings either. This affects **all** trigger
points — `pre_input`, `post_output`, `checkpoint`, `pre_synthesis`.

The comment directly above the early return claims "Governance/trust gates above apply to every
executor kind". That is true of the trust check, which sits above it, and false of decision skills,
which sit below — which is how the gap survived review.

Why it is costly: `required: true` reads as "this gate must pass" and means nothing here;
`swarmkit validate` reports no error because the binding is structurally valid; the trace shows a
normal successful node with no "skipped" marker; and it is executor-dependent, so a topology
validated on a model node changes behaviour when switched to `harness` with no other edit.

**Fixed** by restructuring `node_fn`: `pre_input` moved above the executor dispatch (it gates the
input, and refusing after paying for a harness run would be a strange way to decline), and the
harness result now flows into the shared `post_output` path. The retry re-invokes the **harness**
with the gate's reasoning appended, because `_make_retry_fn` re-prompts a model — which needs a
`model_provider` a harness agent may not have, and would revise the *text* of work done in a sandbox
rather than redo the work. A failed harness is returned ungated: judging an error string and then
paying for retries to fix a sandbox that could not start is not what a conformance gate is for.

`checkpoint` and `pre_synthesis` remain model-path-only — they fire inside task-plan execution,
which a harness node never does. See `design/details/harness-decision-skills.md`.

### 3. `output_schema` ignored (fixed, 1.143.0)

`_harness_node.py` contains zero references to `output_schema`. A harness agent therefore has
neither a schema constraint nor a post-hoc check — gaps 2 and 3 are the two independent mechanisms
that would each have caught a non-conforming output, and on a harness both are absent. This is why
the `wms-design` agent could return markdown where a JSON object was required and the run passed.

**Fixed** at the node, not in the funnel layer as first recommended — a funnel does not cover
`swarmkit run <topology>`, and `output_schema` is declared on the agent, so a contract declared at
the node belongs enforced at the node whichever executor runs it. The correction goes back through
the harness with field-specific errors; exhaustion annotates and emits an auditable
`output.schema_violation`. Only an EXPLICIT schema is enforced: the model path's worker platform
default would otherwise impose a findings-schema on every harness worker, including
`examples/sdlc-pipeline`'s `developer`, which produces a diff. See
`design/details/harness-output-schema.md`.

### 4. `TaskSpec.context_files` is dead (fixed, 1.158.0)

The field exists and is populated with the workspace's `CLAUDE.md`; nothing delivered it. A harness
agent therefore ran without the conventions a model agent is handed, with no warning — the same
assigned-once-read-never shape as gap 6.

**Fixed** by materialising the files into the sandbox working tree, which is where a harness reads
its context from and why `CLAUDE.md` works at all. Two constraints shape it:

*The worktree's own copy wins.* The sandbox is a worktree at `base_ref`, so a committed `CLAUDE.md`
is already present and is the project's own; replacing it with a copy from elsewhere would quietly
change what the agent is told the rules are. Delivery skips existing files.

*A delivered file is excluded from the diff.* `collect_diff` runs `git add --intent-to-add --all`,
so anything the runtime writes would otherwise appear as authored change — and that diff is the
node's output artifact, the next stage's input, and what a human approves at a gate. Presenting a
runtime-written file as the agent's work is the same defect as the display annotation baked into
output text (bug 16), one layer down. Delivery is recorded as `executor.context_delivered`, since
"the agent had the project's rules" is an auditable claim.

Path traversal is refused rather than resolved: context delivery is not a file-write primitive.

### 5. Images (open)

An image reaches the model only as MCP `ImageContent`. Relative paths in a prompt resolve nowhere
inside the harness sandbox (an ephemeral git worktree by default, or `executor.config.working_dir`),
and there is no mechanism that turns a referenced path into an attached image. Gap 1 made this
*visible*; it did not make it work.

## The pattern to watch for

Every one of these is the same shape, and it is the same shape as the degraded checkpointer, the
swallowed `--mcp-config` list and the overwritten per-stage trace:

> **Information exists, nothing surfaces it, and absence renders as success.**

When adding anything to the model path, ask what the harness path does with it. "Nothing, silently"
is a bug even when it is a small one — and when the thing being skipped is a `required: true`
governance gate, the silence is the whole defect.
