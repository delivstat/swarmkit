# Getting an image to a model

How images actually reach an agent in SwarmKit, and the one trap that makes them silently not
arrive. Written up after a design agent spent three runs describing UI screens it had never seen —
convincingly.

The skill route below was verified against runtime 1.129.2; the attachment channel was added in
1.218.0 and is what most callers should use first.

## Two channels, for two different callers

| | Who holds the image | Who decides what to look at | Route |
|---|---|---|---|
| **Attach it to the run** | the caller, before the run starts | the caller | `attachments` / `--attach` — below |
| **Let the agent fetch it** | nobody yet | the model, mid-run | an MCP tool returning `ImageContent` — the rest of this guide |

They do not substitute for each other. A snapshot poller, a webhook with an upload, an application
with a user's screenshot: all of them already hold the bytes and want *one* model call, not an
agent that first has to decide to look. An agent reviewing a ticket with screenshots it has never
seen needs the skill.

## Attach it to the run

Beside the input, not inside it. CLI, one `--attach` per file, repeatable, workspace-relative:

```bash
swarmkit run ./workspace describe-scene \
  --input "What is at the gate?" \
  --attach snapshots/gate.jpg --attach snapshots/gate-wide.jpg
```

HTTP, the same two ways of naming a file:

```json
POST /run/describe-scene
{
  "input": "What is at the gate?",
  "attachments": [
    { "path": "snapshots/gate.jpg" },
    { "data": "<base64 bytes>", "name": "gate-wide.jpg" }
  ]
}
```

| Field | | |
|---|---|---|
| `path` | workspace-relative | **exactly one of** `path` / `data` |
| `data` | base64 | for a caller holding bytes rather than a file — a poller, an upload |
| `name` | optional | display/filename only; derived from `path` when absent |
| `handling` | `preprocess` (default) or `native` | intent for non-image types; inert while only images are carried |

What happens, and what does not:

- **The media type is read from the bytes.** There is no `type` field, and sending one is a 422:
  a caller's claim about content that is about to be forwarded to a third-party model is not
  evidence. This is also why there is one `--attach` and no `--image` / `--pdf`.
- **Images only, today:** `image/png`, `image/jpeg`, `image/gif`, `image/webp`. Anything else is
  refused by name. Per attachment, 20 MiB (`SWARMKIT_ATTACHMENT_MAX_BYTES`).
- **`url` is refused.** The runtime does not fetch caller-supplied addresses — that is the same
  exfiltration primitive the skill route's path-resolution rejects (below). Send the bytes.
- **A bad path is a 422 on the request**, not a job that fails a moment later: a job id means every
  attachment was readable and carryable.
- **It reaches the entry agent's first message and no downstream node.** The root agent sees the
  image in the same model call as the input — no tool round-trip, one pass. A child agent that
  wants it asks through a skill; the runtime does not fan a caller's file out to every node.
- **It is re-read on every turn** of the root's tool loop, so the file has to stay readable for
  the run's duration; that is the other reason streams and URLs are not accepted.
- **Audited, never stored.** Every run writes a `run.attachments` event with name, media type,
  size, SHA-256 and source path — the digest makes the reference checkable later; the bytes never
  enter a log meant to stay readable.
- **Provider coverage follows the family.** Anthropic and every `openai-compatible` provider
  (OpenRouter, Groq, Ollama, llama-server …) get the image part from the same mapping the skill
  route uses; a provider YAML with `capabilities: {images: false}` refuses at the request instead
  of sending bytes a server would drop.

Two edges worth knowing:

- **Harness roots do not receive attachments.** A harness (Claude Code, opencode) reads files
  from its worktree, so an attachment has no message to land in. Put the file in the repository
  the worktree is cut from and name the path in the input, or route through a model agent.
- **Over A2A, a file part becomes an attachment.** A remote caller's `message/send` with a
  `file` part carrying `bytes` reaches the run exactly as `data` does; a `uri` file part is refused
  for the same reason `url` is. The same rules apply when *your* agent calls a remote one through
  an `agent` skill.

That is the whole caller-side story. When the caller is an agent that has to *decide* what to
look at, read on.

## An image in the prompt is still just text

A run's input is a **plain string**, and no schema — topology, archetype or trigger — has an image
or media field. (`executor-adapter` has an `image`, but that is the *container* image for a
sandboxed harness, not a picture.) So for anything not passed as an attachment, neither of the two
obvious approaches works:

- **A path in the prompt** is just text. The model reads the characters; nothing loads.
- **Base64 in the prompt** is tokens. It is never interpreted as an image, because the provider
  only builds an image part from a *tool result* — never from the input string.

For an agent choosing what to look at, the route is **an MCP tool that returns an `ImageContent`
block**:

```
your tool (e.g. docs-reader view_image)
  └─ returns mcp.types.ImageContent(type="image", data=<base64>, mimeType=…)
       └─ langgraph_compiler/_skill_executor.py
            └─ ContentBlock(type="image", image_data=…, image_media_type=…)
                 ├─ model_providers/_openai.py    → {"type": "image_url", "image_url": {"url": "data:…"}}
                 └─ model_providers/_anthropic.py → {"type": "image", "source": {"type": "base64", …}}
```

Provider coverage follows the family: OpenRouter, Groq and every other provider declared over
`openai-compatible` gets the image part from `_openai.py` for free. A provider YAML can switch
it off (`capabilities: {images: false}`) for a server that does not read it.

**Harness executors get there differently** — Claude Code has its own image handling and reads
files from disk directly — but the MCP route works for both, so it is the portable answer.

## The trap: a path that resolves nowhere

`swarmkit docs-reader --workspace <dir>` resolves **relative** paths against that root. Almost
every document extractor writes image references relative to the *document*, not to that root:

```markdown
![](3-5RFComfirmPGM_0.2.xlsx.media/screen1.png)
```

Correct for a web UI serving the ticket. Meaningless to an agent, because docs-reader is rooted at
the repository root and there is no such path there.

**And the failure is silent in the worst way.** The tool reports that the file does not exist; the
model reports *"no screenshot was provided"* and — being helpful — describes the screen from the
surrounding prose anyway. The result reads exactly like a real description. There is no error, no
warning, and nothing in the trace that says an image was missed.

### Fix

**Pass absolute paths, and pass them in the prompt as a list.**

```
SCREENSHOTS — open EVERY one with view-screenshot before writing the screens section.
These are absolute paths and they resolve:
  /abs/path/to/ticket/media/screen1.png
  /abs/path/to/ticket/media/screen2.png
```

Absolute, because the runtime chooses the agent's working directory and a relative path that
resolves differently there is indistinguishable from an absent file.

Rewrite the references inside the document too, if the agent will read the markdown.

!!! note "Since 1.129.2, absolute is not enough on its own"

    `docs-reader` now **confines** reads to its `--workspace` root: an absolute path outside it is
    refused, and so is a `..` traversal or a symlink pointing out (previously both were read, which
    was [the path-confinement fix](https://github.com/delivstat/swarmkit/pull/702)). So the paths
    must be absolute **and** under the workspace root. If your documents genuinely live elsewhere,
    root the server there rather than setting `SWARMKIT_DOCS_READER_ALLOW_OUTSIDE=1`.

## Make opening them non-optional

Availability is not use. Add an instruction that names the consequence:

> The field labels, the button text and the message wording are IN the image and nowhere else. A
> screen described without opening it is invention, however plausible it reads. If a path genuinely
> fails, say so — naming the path — rather than describing the screen anyway.

Also tell it not to translate what it sees. The literal string is what gets built.

## Evidence that it matters

Same requirement, same archetype, same model. Only the paths changed.

| | paths broken | paths absolute |
|---|---|---|
| screens described | 2, invented | **3**, matching the real panels |
| panel titles | generic | `Mobile_Confirm_PGM`, `Mobile_Confirm_Shipment` — only present in the image |
| UI literals captured | 1 | **12**, including `Back`, which appears nowhere in the requirement text |
| `view-screenshot` calls in trace | 0 | 3 |
| tokens | ~10k | ~25k |

`Back` is the tell. It is on the panel and in no prose anywhere, so it could only have come from
the pixels.

Cost roughly 2.5×. Worth it: the alternative was a specification whose UI section was fluent
invention.

## Two practical notes

**Send composed screens, not fragments.** If your extractor composites overlays onto a base
screenshot, send only the composites. Offering both invites the model to describe a stale label
from a fragment as though it were current.

**Budget largest-first if you must budget.** An image costs about 4/3 of its file size once
base64-encoded. If something has to be dropped it should be a fragment, not the screen.

A 64 KiB line limit on harness stdout used to make large images fatal —
`Separator is found, but chunk is longer than limit`. Fixed in 1.129.2; budgets written around it
can be relaxed.

## Checklist

If you hold the file before the run:

- [ ] It is passed as `--attach` / `attachments`, not mentioned in the prompt
- [ ] The consumer is a model agent at the root (a harness root does not receive it)
- [ ] The `run.attachments` audit event shows the expected name, type, size and digest

If an agent has to choose what to look at:

- [ ] An MCP tool returns `ImageContent` — a path or base64 in the prompt does nothing
- [ ] Paths handed to the agent are **absolute**, and under the docs-reader workspace root
- [ ] Image refs inside any document the agent reads are rewritten to absolute too
- [ ] The prompt says to open them, and why
- [ ] The prompt says to report a failed path rather than describe the screen anyway
- [ ] The trace shows the tool actually fired — one call per image, not zero
- [ ] Spot-check one detail that exists only in the pixels

## See also

- [Document reader MCP](../design-notes/document-reader-mcp.md) — the server that provides
  `view_image`, and why a multimodal path exists at all.
- [Building swarms](building-swarms.md) — where skills and MCP servers are granted to an agent.
