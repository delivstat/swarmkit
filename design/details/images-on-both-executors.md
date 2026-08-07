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

**C, with B as a later addition** if callers want to attach up front. Explicitly not A.

C alone fixes the reported failure — an agent asked to review screens can look at them — and B is
additive on top rather than an alternative.

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

A path resolves and returns image content; `..`, absolute paths and symlinks out are refused; a
non-image is refused by content; an oversized file is refused with its size; the audit record
carries the path and the media type and not the bytes; and the same skill on the same file produces
the same content through a model node and through the gateway — the parity assertion this gap
exists for.

## Open question for review

Whether `view-image` ships as a **built-in** skill (available to grant in any workspace) or as a
**reference** skill in `reference/skills/` that a workspace copies. Built-in makes the common case
work with no setup; reference keeps the runtime's built-in surface small. I lean built-in, on the
grounds that "look at this image" is not a workspace-specific capability — but it is a surface
decision and belongs to whoever owns that line.
