# Images reach an agent the same way on both executors

**Status:** proposed — design only

## Goal

Make "look at this image" work, and work identically whichever executor runs the node.

## Where it stands

An image reaches a **model** agent by exactly one route: an MCP tool returns `ImageContent`, and
`_skill_executor` turns it into a real image block that `_anthropic` / `_google` / `_ollama` render.

A **harness** agent gets the same thing through the gateway — since 1.138.0, `_to_content` carries
`ImageContent` rather than dropping it. So *tool-returned* images work on both paths.

What does not work, on either path, is a **path in a prompt**:

- On the model path, `docs/screens/login.png` in the input is text. Nothing resolves it, nothing
  attaches it. The model is asked about a picture it was never shown.
- On the harness path it is worse: the sandbox is a git worktree at `base_ref`, so a file that is
  untracked, uncommitted, or outside the repo is not there at all. A harness with a perfectly good
  `Read` tool opens nothing.

Gap 1 made this *visible* — a failed image tool is no longer traced as a success — it did not make
it work.

## Why this is the harness gap that needs a mechanism

The other five parity gaps were plumbing: a field computed and dropped, a code path that skipped a
check. Each was fixed by grepping for a name and counting its readers. There is nothing to un-drop
here. Something has to decide *which* bytes become an image, and that decision does not exist yet.

## Options

### A. Resolve paths out of the prompt, automatically

Scan the input for things that look like image paths, resolve them against the workspace, attach.

Rejected. Two problems, either fatal:

*It guesses.* Prose contains paths that are not attachments — a filename in an error message, a
path being discussed rather than shown. A false positive silently feeds an unrelated file to a
model, and the failure is invisible: the agent answers about the wrong picture.

*It is a prompt-driven file read.* The input to a run is frequently untrusted — a ticket body, a
webhook payload, an upstream stage's artifact. Turning text in it into "read this file and send it
to a model" is an exfiltration primitive. Any path-traversal defence still leaves every readable
image in the workspace reachable by anything that can write a ticket title.

### B. Declare attachments explicitly on the run

An `attachments:` list beside the input, resolved by the runtime.

Safe and unambiguous, and it fits how `swarmkit run` and `POST /run/{topology}` are already called.
But it only helps a caller who knows what to attach up front, and it does nothing for an agent that
decides mid-run that it needs to look at something.

### C. A built-in `view-image` capability skill  ← recommended

A skill that takes a workspace-relative path and returns `ImageContent`.

This is the answer the architecture already implies. Skills are the capability primitive (invariant
5); the return type is the one both executors already handle; it is governed and audited like every
other skill; and the agent asks for a specific file rather than the runtime guessing from prose.

It also gets the parity property for free — the gateway already carries `ImageContent` — which is
what this gap is fundamentally about.

The sandbox problem does not arise: the skill reads on the *runtime* side and returns bytes, so it
does not matter that the file is absent from the worktree.

## The recommendation

**C and B together.** Explicitly not A.

*(Revised. This note originally read "C, with B as a later addition" — B was never rejected, only
deferred for want of a caller who needed it. One turned up, and it is the case B is exactly shaped
for, so "later" is now.)*

C fixes the reported failure — an agent asked to review screens can look at them. B fixes a
different one, and the two do not substitute for each other:

| | The caller | Who decides what to look at |
| --- | --- | --- |
| **C** — `view-image` skill | an agent, mid-run | the model, from a path it names |
| **B** — `attachments:` on the run | code, before the run starts | the caller, which already holds the bytes |

### The caller that makes B not-optional

Minder's alert path (`~/minder`, `design/inference-backends.md`). A snapshot poller detects an
event, grabs one JPEG, and wants a grounded one-sentence description from a local VLM. It is holding
the image at the moment it decides to describe. Routing that through C costs it:

- **two model passes instead of one** — agent calls `view-image`, then describes — where a single
  pass on `qwen2.5vl:3b` on CPU already costs 60–70 s on that box;
- **a correct tool call out of a 3B**, on an appliance where 3B tool-calling is already known to
  break under `output_schema`.

So a deterministic caller with bytes in hand is pushed off topology runs altogether, and with it
goes the audit record, the governance gate on cloud egress, and prompts-as-configuration — the three
things routing through the runtime is *for*. That is the framework charging an appliance two
inference passes to be observable, which is the wrong way round.

Generalised, B's caller is anything holding bytes before a run: a webhook with an uploaded photo, a
trigger firing on a new file, an application calling `POST /run/{topology}` on a user's screenshot.
None of them want an agent to go and find what they are already carrying.

## What B has to get right

B has shipped once and been reverted, so this section is mostly *not repeating that*.

**Attachments are not state.** The M8 implementation put `image_paths` on `SwarmState` and
broadcast it; `c408804a` then had to narrow injection to leaf agents because handing an image to a
text-only supervisor is an API error, and `43ed71e3` reverted the lot. The lesson is that an
attachment is an argument to *one* invocation, not ambient run state every node inherits. It belongs
in the entry node's first user message and nowhere else; a node that wants an image it was not given
uses C, which is what C is for.

**Declared on the call, resolved by the runtime.** `swarmkit run --image <path>` (repeatable),
`attachments: [...]` in the `POST /run/{topology}` body, and the equivalent argument on the Python
entry point. No new schema field on topology, archetype or trigger — this is a property of an
invocation, not of an artifact.

**The same safety rules as C, because it is the same act.** Workspace-relative; `..`, absolute paths
and symlinks out refused rather than resolved; a stated size ceiling refused with its size; non-images
refused **by content, not extension**. A caller-supplied path is no more trustworthy than a
model-supplied one, and the checks should be the same code.

**Harness parity, or say plainly that it is model-only.** This note exists because images should
reach an agent identically on both executors. For a model node an attachment is a `ContentBlock`.
For a harness the honest mechanism is `context_files` (1.158.0) — the attachment is materialised
into the worktree and named. If that is not in scope for the first cut, the note should say
"model-only, harness follows" rather than leaving a reader to discover it.

**The audit record says what was attached** — path, media type and size on the run record, never the
bytes. Same rule as C's `skill.executed`.

**A text-only model given an attachment fails loudly.** The original bug was an API error from
passing an image to a supervisor. With attachments no longer broadcast the case is rarer, but a
caller can still attach to a topology whose entry node is text-only. Refuse at the boundary, naming
the model, rather than surfacing a provider error.

## What C has to get right

- **Workspace-relative, and the escape refused rather than resolved.** Same rule as
  `deliver_context_files`: `..` and absolute paths are dropped with a warning. A capability that
  reads a named file is not a capability to read any file.
- **Declared, not ambient.** The skill is granted per archetype like any other. An agent that was
  not given it cannot look at images, and that is the point of a grant.
- **A size ceiling, stated.** A 40 MB screenshot is a cost and a context problem. Refuse above a
  bound with a message saying so, rather than sending it and letting a provider reject it.
- **Non-images refused by content, not by extension.** `.png` on a text file should fail as "not an
  image", not arrive as corrupt base64.
- **The audit record says what was read.** `skill.executed` already carries inputs and outputs
  (1.153.0); the path belongs in the inputs, and the outputs should record the size and media type
  rather than the bytes.

## Non-goals

- Not OCR, not description, not any interpretation. The skill delivers pixels; the model looks.
- Not a general file-read skill. Images have a content type both executors can carry; arbitrary
  files do not, and the honest way to hand a harness a file is `context_files` (1.158.0).
- Does not change how tool-returned images work. That path is correct and stays.

## Test plan

**C.** A path resolves and returns image content; `..`, absolute paths and symlinks out are refused;
a non-image is refused by content; an oversized file is refused with its size; the audit record
carries the path and the media type and not the bytes; and the same skill on the same file produces
the same content through a model node and through the gateway — the parity assertion this gap
exists for.

**B.** An attached image reaches the entry node's first user message; the same path through `--image`,
the HTTP body and the Python entry point produces an identical request. **Attachments do not appear
in any downstream node's messages** — the assertion that stops the reverted design coming back, and
the one that matters most, since the failure it prevents is an API error at a supervisor. The
resolution and refusal cases are asserted against the *same* helper as C, so the two cannot drift.
A topology whose entry node is a text-only model refuses an attachment at the boundary, naming the
model, rather than emitting a provider error. The audit record carries path, media type and size.
A run with no attachments is byte-identical to today.

**Together.** One run where the caller attaches a photo *and* the agent calls `view-image` on a
second file, asserting both arrive and are distinguishable in the audit record.

## Sequencing

**B and C ship before the caller that needs them changes.** Minder's move onto topology runs
(`~/minder`, `design/inference-backends.md`, Phase 0b) is blocked on B; doing it in the other order
means either building a two-pass workaround that gets deleted, or leaving the vision path
unobservable for another release. C is independent and can land first or alongside.

## Open questions for review

1. Whether `view-image` ships as a **built-in** skill (available to grant in any workspace) or as a
   **reference** skill in `reference/skills/` that a workspace copies. Built-in makes the common case
   work with no setup; reference keeps the runtime's built-in surface small. I lean built-in, on the
   grounds that "look at this image" is not a workspace-specific capability — but it is a surface
   decision and belongs to whoever owns that line.
2. **Does B cover the harness executor in the first cut, or is it model-only?** The `context_files`
   route exists and is the honest mechanism, but materialising an attachment into a worktree is more
   than a `ContentBlock`. Model-only first is defensible; leaving it unstated is not, since this
   note's whole title is about parity.
3. **Attachments beyond images?** A PDF or a CSV attached to a run is the obvious next request, and
   `context_files` already covers the harness side of it. Deliberately out of scope here — images
   have a content type both executors carry, arbitrary files do not — but the field name
   `attachments:` invites the question and should be chosen knowing that.
