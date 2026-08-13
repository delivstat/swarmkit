# Declarative skill prerequisites (`requires:`)

**Status:** proposed — design only. Filed from the tlgsg-wms workspace against 1.180.0.

## Goal

Let a topology say that one skill call is a precondition of another, and have the runtime enforce
it — so ordering is a reviewable property of the workspace rather than a request in a prompt.

## The evidence, and why it is decisive

Three attempts in one workspace, all failing the same way: an archetype mandating
`list_build_conventions` produced **0** calls; 25,804 characters of convention index injected at
`pre_input` were demonstrably ignored; and `get_estate_reference` — granted, documented, and
instructed with the reason attached — was called **0** times.

The controlled comparison is the one that settles it. In a **single run, same agent, same prompt**,
the ack-gated `get_build_convention` was called 4 times and the merely-requested
`get_estate_reference` 0 times. The variable is not the prompt; it is whether the tool *refuses
service*.

That is the same finding as "tool names beat prompts", one level up: a model follows mechanism over
instruction, so ordering has to be mechanism.

## What exists today, and why it is not the answer

An HMAC handshake in `mcp-servers/_ack.py`: `get_build_convention` mints a token, every estate tool
refuses without one. It works — and it is in the wrong place. The constraint lives across seven
server files, is invisible to `swarmkit validate`, threads an `ack` argument through every tool
schema the agent sees, and couples the runtime to the servers by a shared secret.

## The mechanism already exists

Both executors dispatch through **one function**:

- model path — `_skill_executor.py:125` → `check_mcp_permission`
- harness path — `_gateway.py:240` → `governed_mcp_call` → `check_mcp_permission`

It already returns `(allowed, reason)`, and both paths already surface that reason to the agent as a
tool error it can act on. **A prerequisite is a second reason to deny.** One enforcement point covers
both executors, and the recoverable-refusal behaviour that makes the ack gate work is the behaviour
that is already there.

Refusals are already shaped for the audit log too: a denied gateway call has emitted
`skill.executed` with `policy_decision="deny"` since 1.177.0.

## Where the constraint is declared

`skills` is currently `array<identifier>` with `x-swarmkit-ref: skill`, which drives reference
validation and codegen. The request proposes a mixed list of strings and single-key maps. Two shapes:

**A — inline on the grant** (as proposed)

```yaml
skills:
  - list-build-conventions
  - get-build-convention:
      requires: [list-build-conventions]
```

**B — a sibling block, `skills` unchanged**  ← recommended

```yaml
skills: [list-build-conventions, get-build-convention, search-solution-code]
requires:
  get-build-convention: [list-build-conventions]
  search-solution-code: [get-build-convention]
```

B keeps `skills` a plain identifier array — no mixed-type list for every consumer and the pydantic
model to handle, no disturbance to `x-swarmkit-ref`. Duplicates become impossible because it is a
map. And it puts the ordering rules **in one readable block**, which is half the stated value: *"a
reader of the topology cannot see that an ordering rule exists."* Scattered through a list, they are
as easy to miss as they are in the servers.

A reads better at the point of use, and that is a real argument. It is a surface decision — see the
open questions.

## Semantics

- **Per `(run, agent)`, not per run.** The request says "same run"; the reasoning in it says
  otherwise — *"continue in the same session with that content live in its context"*. If agent A read
  the card, agent B does not have it in context, and a run-scoped set would let a parallel sibling
  satisfy a prerequisite it never saw.
- **Satisfied by a prior successful call.** An exception or an MCP `isError` result does not satisfy.
- **Enforced at dispatch**, before the server is touched, and **recoverable**: the agent calls the
  prerequisite and retries within the same loop.
- **Order-independent among peers.** `requires: [a, b]` means both, in any order.

### The error message is the mechanism

The ack gate works because its refusal is *actionable*. A generic "permission denied" invites
give-up or thrash. The message names the missing prerequisite and says what to do:

```
get-build-convention requires list-build-conventions, which has not been called in this
session. Call list-build-conventions first, then retry.
```

This is specified and tested rather than left to implementation, because it is the part doing the
work.

### What "successful" can honestly mean

The runtime sees exceptions and the MCP `isError` flag. **A tool that returns prose saying "not
found" as a successful result will satisfy its prerequisite.** Criterion 3 holds for real failures
and not for tools that report failure in their payload; better to state that than to imply a
guarantee the seam cannot give.

## Validation

- A `requires:` naming a skill the agent does not hold is a **resolution error** — the reviewable
  half of the feature, and the check that makes the block trustworthy.
- **A cycle is a resolution error.** `a requires b`, `b requires a` permanently blocks both, and the
  agent can never recover. The request does not mention cycles; unchecked, this feature can render
  an agent unable to act with no error anywhere.

## What lands in the trace

A refusal emits `skill.executed` with `policy_decision="deny"` and a reason naming the prerequisite —
distinguishable from an ordinary tool error, per criterion 5. This is what makes the gate
*measurable*: today only calls that happened can be counted, and **a gate that is working looks
exactly like a gate that is never reached** until refusals are recorded.

## Scope

`requires:` guards **skill invocations that pass through the MCP permission seam**. A capability
skill that is not an `mcp_tool` dispatches elsewhere and is not covered by this note — either the
scope is stated narrowly, or a second enforcement point is needed. Naming the boundary matters more
than widening it silently.

It also needs a `(server_id, tool_name) → skill_id` mapping at dispatch, since `requires` names
skills and the seam sees tools. The grant carries both, but this is the fiddliest part of the
implementation.

## Non-goals

- **Not parameterised in v1.** `requires: [get-build-convention(kind=$kind)]` binds an argument of
  the guarded call to an argument of the prerequisite call — a constraint expression language, and
  once `$kind` exists `$solution` follows (the ack tokens are already per-`(kind, solution)`), then
  conditionals. Ship unparameterised, and **design the data shape so the parameterised form is
  additive rather than a rewrite**. State the gap honestly: unparameterised, reading *a* console card
  satisfies RF work.
- **Not guarding decisions.** The request draws this line correctly: "read the argument reference
  before writing `<Args>`" has no tool call to attach to. That is boundary validation, and
  `scripts/write-solution-config.py` already does it.
- **Not cross-agent.** A prerequisite is about what is in *this* agent's context.

## Considered and rejected: hiding the tool until it is unlocked

Do not advertise a tool until its prerequisite is satisfied — the model cannot call what it cannot
see. Strictly stronger enforcement, and worse: **undiscoverable.** The agent never learns the tool
exists, so it cannot know to call the prerequisite, and the refusal that teaches it is exactly the
mechanism that made the ack gate work. Recorded here so it is not later mistaken for an improvement.

## Test plan

- A guarded call is refused until its prerequisite has succeeded in the same `(run, agent)`, and the
  refusal names the prerequisite.
- **The agent recovers within one loop**: prerequisite, retry, success — asserted through a real
  tool loop, not a unit call, because recoverability is the whole feature.
- A prerequisite that raised does **not** satisfy the requirement.
- A sibling agent's call does not satisfy this agent's prerequisite.
- `requires: [a, b]` is satisfied in either order and not by one alone.
- Enforcement is identical on the model path and through the gateway — one seam, asserted twice.
- A refusal appears in the audit log as `policy_decision="deny"` naming the prerequisite, and is
  distinguishable from a tool that simply failed.
- `swarmkit validate` fails on a `requires:` naming an ungranted skill, and on a cycle.
- **A topology with no `requires:` produces a byte-identical run** — asserted against a reference
  workspace, not a fixture.

## Demo plan

The reported failure, reproduced and then fixed: an agent granted `get-build-convention` with
`requires: [list-build-conventions]` calls it first, is refused, calls the prerequisite, retries, and
succeeds — with the refusal visible in `swarmkit logs` and the whole ordering rule visible in four
lines of the topology. Alongside it, the same workspace with `_ack.py` deleted.

## Open questions for review

1. **Shape A or B?** B keeps `skills` a plain array and gathers the rules where a reviewer can see
   them; A reads better at the point of use. A surface decision.
2. **Should a refusal be capped?** An agent that loops refuse → wrong call → refuse burns tokens. A
   per-skill refusal ceiling that fails the run with a clear reason may be kinder than an infinite
   polite "no".
3. **Does `requires:` belong on the grant only, or also on the skill definition** as a default a
   grant can override? The request says grant-only and gives the right reason — ordering is a
   property of how this workspace works — but a skill whose author knows it is meaningless without a
   predecessor may want to say so.
4. **Is MCP-only scope acceptable for v1**, or does a non-MCP capability skill need the same guard
   before this is worth having?
