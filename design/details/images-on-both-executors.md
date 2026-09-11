# Attachments reach an agent the same way on both executors

**Status:** proposed — design only
*(Filename unchanged — this began as an images note and generalised. Links are by path.)*

## Goal

Make "look at this" work, and work identically whichever executor runs the node — for **any media
type the target can actually accept**, not images alone.

Images are the first case and the one that motivated the note, but nothing in the mechanism is
image-shaped. A PDF, an audio clip and a video are the same problem: bytes a caller or an agent has,
which have to reach a model or a harness in the form that target understands, or be refused by name
if they cannot.

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
here. Something has to decide *which* bytes become an attachment, and that decision does not exist yet.

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
| **C** — `view-file` skill | an agent, mid-run | the model, from a path it names |
| **B** — `attachments:` on the run | code, before the run starts | the caller, which already holds the bytes |

**Both generalise past images together.** The Options section above was written when this note was
image-only, so it names the skill `view-image` and its return type `ImageContent`. With attachments
typed (§"Any type") the skill is `view-file`, returning the MCP content type matching the bytes,
and its result goes through the same provider mapping as B's — including the same `else: raise` when
a provider has no shape for it. One media path, two entry points; not two mechanisms with separate
type handling.

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

## Scope: images now, everything else by preprocessing

Two decisions taken together, because the sections that follow are long enough to obscure how small
the actual first cut is.

**Images ship first, alone, and they need none of this machinery.** All four adapters already
implement image blocks — `_ollama.py:107`, `_anthropic.py:127`, `_openai.py:158`, `_google.py:118`.
There is no divergence to reconcile, no capability question, no adapter state. B for images is
wiring an attachment into a `ContentBlock(type="image")` that already exists and already works on
every provider. That is the whole of what the first caller (Minder's alert path) needs, and it is
the whole of what should land before it.

**Everything else is normalised at the SwarmKit end, not mapped per provider.** The four shapes
catalogued below are real and they are a lot of work — the correct response is to not do most of it.
Preprocess an attachment into the two things *every* provider already takes, text and images:

| Attached | Preprocessed to | Reaches |
| --- | --- | --- |
| image | image, untouched | all four adapters, today |
| PDF, docx, xlsx | text — **MarkItDown, already in `docs_reader`** | everything, Ollama included |
| audio | text — Whisper, local-capable on CPU | everything |
| video | frames + transcript | everything |

One code path, no per-provider divergence, no capability question, and it is already the house
style rather than a new idea. Native passthrough — Anthropic's `document`, OpenRouter's
`file-parser`, Moonshot's Files API — becomes an **opt-in for the cases where fidelity genuinely
beats text**: scanned pages, charts, layout-heavy documents. Those cases are real, and they are not
the common one.

**Sniffing and explicitness answer different questions, so both.** The media *type* is sniffed from
the bytes, because a caller should not be trusted about what it is sending to a provider. The
*handling* is declared — `preprocess` (default) or `native` — because that is an intent, not a claim
about content, and it is the knob that decides whether a PDF becomes text or goes native.

**Latency, since it is the reason any of this matters for a local-model caller.** Preprocessing cost
lands on PDFs and audio. An image is passed through untouched, so an attached JPEG to a local VLM
costs nothing beyond the run itself — which is noise against a 60–70 s CPU pass. The preprocessing
route does not tax the path that needs to be fast.

## Any type: one verb, and no capability table

The caller's surface is **one thing**: attach a file. No `--pdf`, no `--audio`, no declaring what
the bytes are, and **no pre-flight check against a list of what the target supports**. Attach it,
and if the target cannot handle it, the failure comes back from the target.

That is deliberately less machinery than an earlier draft of this section proposed, and the reasons
the capability table was wrong are worth keeping written down:

- **Support is model-granular, not provider-granular.** Anthropic takes PDFs on some models; OpenAI
  takes audio only on audio-capable ones. A table keyed by provider is simply incorrect, and one
  keyed by model goes stale every time a vendor ships.
- **A false refusal is worse than a provider error, because we own it.** A vendor adds a type, our
  table still says no, and the user is blocked by SwarmKit rather than by the model.
- The provider's own error is usually clearer than anything synthesised from a table.

### But the runtime still has to know the type — that part is not optional

Not policy: **wire format**. Three of the four providers need a different shape per type, so
something has to choose one before serialising.

| Provider | Image today | A PDF would need |
| --- | --- | --- |
| **Ollama** | `entry["images"] = [...]` — a top-level array on the message | **nothing exists.** There is no field to put it in |
| **Anthropic** | `{"type": "image", "source": {...}}` | `{"type": "document", ...}` — a *different block type* |
| **OpenAI** | `{"type": "image_url", "image_url": {...}}` | `input_audio` for audio, Files API for documents |
| **Google** | `Part(inline_data=Blob(mime_type=..., data=...))` | **nothing** — one uniform shape, `mime_type` is a field |

So the media type is **sniffed from the file's content** at attach time — not taken from a flag
name, an extension, or the caller's word for it — and each provider maps it to its own shape. Only
Google is genuinely "just attach" as-is.

### Mapping is not always a pure function — some providers need a round-trip first

The table above makes it look as though every adapter turns a block into JSON and sends it. Two
providers do not, and designing as if they did would leave a real capability unreachable:

| Shape | Who | What the model ends up seeing |
| --- | --- | --- |
| **1. Inline content block** | Anthropic `document`, Google `inline_data`, every image path | the raw bytes |
| **2. Router block, parsed server-side** | OpenRouter `{"type":"file"}` + its `file-parser` plugin | bytes or extracted text, per engine |
| **3. Out-of-band upload → id → extract → inject** | **Moonshot (Kimi), OpenAI Files** | **text** |
| **4. Converted client-side before it is ever an attachment** | MarkItDown, Whisper | text |

**Shape 3 is the one that breaks the model.** Moonshot's Kimi does take PDFs — via
`POST /v1/files` with `purpose="file-extract"`, then fetching the extracted content by id and
putting *that* in `messages`. (The same purpose covers image and video understanding, so it is
effectively a conversion service rather than a document feature.) OpenAI's Files API has the same
shape.

That is a **separate API call before the chat call**, plus file lifecycle — upload, reuse across
turns rather than re-uploading per message, and eventually delete. An adapter doing this holds
state. So "one line per adapter" is true of shapes 1 and 2 and not of 3, and an adapter interface
that assumes `block → dict` has no room for it.

The consequence for the interface is small but has to be deliberate: **mapping happens inside the
provider's async request path, not in a pure serialiser**, so an adapter is free to make calls of
its own before composing the request. Nothing about the caller's surface changes — still one
`attach` verb — and shapes 1, 2 and 4 pay nothing for shape 3 existing.

### The one rule that survives: a provider never drops what it cannot map

`_ollama.py` builds `entry["images"]` from image blocks. Handed a PDF it has nowhere to put it, and
the tempting `if block.type == "image"` filter would send the message **without** the attachment —
an agent answering confidently about a document it never received, which is the failure in this
note's first paragraph.

So every adapter's mapping ends in `else: raise`, naming the provider and the media type: nothing to
maintain, nothing to go stale, and no way to accidentally widen or narrow what is accepted. From the
caller's side it is indistinguishable from "the model rejected it" — the error simply arrives a
layer earlier, because for Ollama the bytes would never have reached a model to be rejected.

The rule is about the **fallthrough**, not about the size of the mapping: a shape-3 adapter may run
an upload and an extraction before it composes a request, and still ends in the same `raise` for a
type it has no route for.

**This is what the title's "same way on both" can honestly claim.** Both executors take an
attachment by the same verb. Whether a given target can *do* anything with a given type is between
the caller and that target — a harness reads a PDF, Ollama does not, and no plumbing changes that.
What the framework guarantees is that the answer is never silence.

### `ContentBlock` has to stop being image-shaped

`type` is `Literal["text", "tool_use", "tool_result", "image"]`, with dedicated `image_data` /
`image_media_type` fields. A media block should carry `media_type` and `data` generically, with
`type: "media"` (or per-kind `document` / `audio` / `video`, if the provider mappings read better
that way). The existing image fields stay as deprecated aliases so no provider breaks on the same
commit.

**Three routes per type, chosen deliberately, and we already own the third.** For any given media
type an attachment can reach a model as a native block, reach a harness as a file, or be
**converted to text first** — and `docs_reader` already integrates MarkItDown for exactly that.
Documents in particular have a good text answer, so the decision per type is a real one and not
automatically "add a native block". Native where the provider is genuinely better at the raw bytes
(a scanned page, a chart); MarkItDown where the text is the content.

**Named because it is a real unlock, not just tidiness:** Google accepts **video** natively, and
Minder already produces clips with ffmpeg. "What happened in this clip" becomes answerable on a
cloud tier and stays impossible on the local one — an honest capability split the matrix above makes
visible up front, rather than a mystery at runtime.

## What B has to get right

B has shipped once and been reverted, so this section is mostly *not repeating that*.

**Attachments are not state.** The M8 implementation put `image_paths` on `SwarmState` and
broadcast it; `c408804a` then had to narrow injection to leaf agents because handing an image to a
text-only supervisor is an API error, and `43ed71e3` reverted the lot. The lesson is that an
attachment is an argument to *one* invocation, not ambient run state every node inherits. It belongs
in the entry node's first user message and nowhere else; a node that wants an image it was not given
uses C, which is what C is for.

**Declared on the call, resolved by the runtime.** `swarmkit run --attach <path>` (repeatable),
`attachments: [...]` in the `POST /run/{topology}` body, and the equivalent argument on the Python
entry point. `RunRequest` in `server/_schemas.py` is a plain pydantic model, so this is a field on
the **invocation** — no new field on the topology, archetype or trigger schemas, because an
attachment is a property of a call, not of an artifact.

```jsonc
{
  "input": "What changed at the gate?",
  "attachments": [
    { "path": "snapshots/gate-1732.jpg" },                        // step 1
    { "data": "<base64>", "name": "q3.pdf", "handling": "native" } // step 3+
  ]
}
```

| Field | | |
| --- | --- | --- |
| `path` | workspace-relative | **exactly one of** `path` / `data` |
| `data` | base64 | for a caller holding bytes rather than a file |
| `name` | optional | display/filename — real caller metadata, since some providers want one. Derived from `path` when absent. **Not** a type claim |
| `handling` | `preprocess` (default) \| `native` | intent, not content. **Arrives in step 3** — step 1 has no use for it, because an image is never preprocessed |

**There is no `type` field, deliberately.** The media type is sniffed from the bytes, carried on the
resolved attachment and written to the audit record — but never accepted from the caller. Taking it
as input invites a claim about bytes we are about to forward to a third party, and the flag-name
version of the same mistake (`--image`, then `--pdf`, `--audio`, `--video`) bakes the first case into
the surface forever. Hence one `--attach`. (`--image` may survive as a deprecated alias; it shipped
once in M8 and may be scripted somewhere.)

**No `stream`, and no `url`.** Both are tempting and both are wrong here.

An attachment must be **re-readable**: the tool loop re-sends the whole message history each turn, a
shape-3 adapter reads the bytes before uploading, and a retry re-sends everything. A stream is
single-consumption, so accepting one means the runtime buffers it anyway — hiding the memory cost
rather than avoiding it, and turning a clear boundary error into "stream already consumed" on turn
two. It also buys nothing at the wire, since every provider wants base64 in a JSON body, which is
buffered by construction. A caller holding a stream materialises it, which at least puts the
buffering where someone can see it.

A `url` source would have the runtime fetch a caller-supplied address server-side — the same
exfiltration class this note rejects Option A for, and worth naming explicitly because OpenRouter
*does* accept URLs for PDFs, so passing one through will look like a free feature to somebody later.

**The same safety rules as C, because it is the same act.** Workspace-relative; `..`, absolute paths
and symlinks out refused rather than resolved; a stated size ceiling refused with its size; content
that does not match what it claims to be refused **by bytes, not extension**. A caller-supplied path
is no more trustworthy than a model-supplied one, and the checks should be the same code.

**Harness parity, or say plainly that it is model-only.** This note exists because images should
reach an agent identically on both executors. For a model node an attachment is a `ContentBlock`.
For a harness the honest mechanism is `context_files` (1.158.0) — the attachment is materialised
into the worktree and named. If that is not in scope for the first cut, the note should say
"model-only, harness follows" rather than leaving a reader to discover it.

**The audit record says what was attached** — path, media type and size on the run record, never the
bytes. Same rule as C's `skill.executed`.

**A text-only model given an attachment fails loudly — and the provider's error is the right one.**
The original bug was an API error from passing an image to a supervisor. With attachments no longer
broadcast the case is rarer, but a caller can still attach to a topology whose entry node is
text-only. That error is allowed to surface as the provider raised it: it is accurate, it is
model-specific in a way nothing here can be, and it is not silence. No pre-flight check against a
list of which models take pictures — see §"Any type" for why that list would be wrong.

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

- Not OCR, not description, not any interpretation. The mechanism delivers bytes; the model looks.
  Conversion-to-text is MarkItDown's job and stays there.
- **Not a general file-read capability.** The types a target *declares* it accepts are reachable;
  everything else is refused by name. "Any type the model or harness supports" is a matrix, not a
  licence to hand any file to anything — and for a harness the existing route is `context_files`
  (1.158.0), not a new one.
- Not a new transport for large payloads. The size ceiling applies per attachment regardless of
  type, and a 400 MB video is refused with its size like anything else.
- Does not change how tool-returned content works. That path is correct and stays.

## Test plan

**C.** A path resolves and returns media content; `..`, absolute paths and symlinks out are refused;
a file whose bytes do not match its declared type is refused **by content, not extension**; an
oversized file is refused with its size; the audit record carries the path and the media type and
not the bytes; and the same skill on the same file produces the same content through a model node
and through the gateway — the parity assertion this gap exists for.

**Never dropped — the assertion that keeps the generalisation honest.** For **every** provider, a
media block whose type that provider has no shape for **raises**, naming the provider and the media
type. The load-bearing case is Ollama with a PDF: assert the request is not sent with the attachment
quietly filtered out. Written as a loop over the provider adapters, so a provider added later fails
this test until it decides what it does with an unmappable type — the one way to keep it from being
forgotten.

**Preprocessing produces the same request on every provider.** A PDF attached with the default
handling reaches all four adapters as identical text; the same file with `native` reaches only the
adapters that have a shape for it, and raises on the rest. This is the assertion that the
preprocessing route actually removes the divergence rather than adding a fifth shape to it.

**An image is never preprocessed.** Asserted, because it is the latency-sensitive path: the bytes
attached are the bytes sent, and no conversion, re-encode or resize sits between them.

**A shape-3 adapter uploads once, not per message.** *(Only when native handling ships.)* With a
file attached and a multi-turn tool loop running, the upload happens once and the returned id is
reused; asserted by counting calls against a stubbed Files endpoint. Getting this wrong is not a
correctness bug that shows up in a test — it is a bill, which is why it is asserted rather than
reviewed.

**Type comes from content.** A PNG named `.pdf` maps as an image; a text file named `.png` does not
become a corrupt image block. Sniffed once at attach time and carried on the block, so B and C
cannot disagree about what a file is. A request that supplies a `type` is rejected as an unknown
field rather than honoured — the field does not exist, and pydantic should say so.

**`path` XOR `data`, and neither `stream` nor `url`.** Both-or-neither is a 422 naming the
attachment. A `url` source is rejected with a message pointing at the reason rather than a schema
error, since the next person to want it will assume it was an oversight.

**An attachment survives a multi-turn loop.** Two turns of tool calls with an attachment present:
the bytes are still in the request on turn two, from the same in-memory attachment, without
re-reading the file. This is the assertion that would have caught a stream sneaking in.

**B.** An attached image reaches the entry node's first user message; the same path through `--attach`,
the HTTP body and the Python entry point produces an identical request. **Attachments do not appear
in any downstream node's messages** — the assertion that stops the reverted design coming back, and
the one that matters most, since the failure it prevents is an API error at a supervisor. The
resolution and refusal cases are asserted against the *same* helper as C, so the two cannot drift.
The audit record carries path, media type and size. A run with no attachments is byte-identical to
today.

**Together.** One run where the caller attaches a photo *and* the agent calls `view-file` on a
second file, asserting both arrive and are distinguishable in the audit record.

## Sequencing

Deliberately staged so the blocking piece is the small one.

1. **B, images only.** `--attach` / `attachments:` into the existing `ContentBlock(type="image")`.
   No new provider code — every adapter already handles it. This is the whole of what unblocks
   Minder's Phase 0b, and it should not wait behind anything below.
2. **C, `view-file`.** Independent of B; can land alongside or after.
3. **Preprocessing.** MarkItDown for documents (already vendored in `docs_reader`), Whisper for
   audio. One conversion path, no per-provider work. Ships when a caller attaches something that is
   not an image.
4. **Native passthrough, per provider, opt-in.** Shapes 1–3 below. Only for the cases where fidelity
   beats text — a scanned page, a chart, a layout-heavy document — and only for providers where
   someone has asked. Explicitly **not** a completeness exercise: an unmapped type raises, which is
   a working outcome.

**Step 1 blocks Minder; steps 3 and 4 block nothing.** Reading this note as one deliverable is the
mistake it is arranged to prevent — the provider divergence catalogued below is real, and almost
none of it is on the critical path.

## Open questions for review

1. Whether `view-file` ships as a **built-in** skill (available to grant in any workspace) or as a
   **reference** skill in `reference/skills/` that a workspace copies. Built-in makes the common case
   work with no setup; reference keeps the runtime's built-in surface small. I lean built-in, on the
   grounds that "look at this image" is not a workspace-specific capability — but it is a surface
   decision and belongs to whoever owns that line.
2. **Does B cover the harness executor in the first cut, or is it model-only?** The `context_files`
   route exists and is the honest mechanism, but materialising an attachment into a worktree is more
   than a `ContentBlock`. Model-only first is defensible; leaving it unstated is not, since this
   note's whole title is about parity.
3. ~~Attachments beyond images?~~ **Resolved: yes — one `attach` verb, any type, no capability
   table.** See §"Any type". What remains open underneath it is narrower:
   **(a)** whether the generalised block is one `type: "media"` or per-kind `document` / `audio` /
   `video`, which is a question about which shape the provider mappings read better in;
   **(b)** which provider mappings ship in the first cut. Image is done everywhere; PDF is the
   obvious second (Anthropic's `document` block, Google's `inline_data`); audio and video are
   Google-shaped today. An unmapped type is not a gap to close before shipping — it raises, by the
   rule above, which is a working outcome rather than a missing feature.
