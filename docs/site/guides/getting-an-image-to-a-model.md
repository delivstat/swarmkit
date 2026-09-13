# Getting an image to a model

How images actually reach an agent in SwarmKit, and the one trap that makes them silently not
arrive. Written up after a design agent spent three runs describing UI screens it had never seen —
convincingly.

Verified against runtime 1.129.2; the attachment channel below was added later.

## Two channels, for two different callers

**If you already have the file, attach it to the run.** A caller that holds the bytes — a snapshot
poller, a webhook with an upload, a script — passes them beside the input:

```bash
swarmkit run ./workspace describe-scene --input "What is at the gate?" --attach snapshots/gate.jpg
```

```json
POST /run/describe-scene
{ "input": "What is at the gate?", "attachments": [{ "path": "snapshots/gate.jpg" }] }
```

The file reaches the **entry agent's first message** — one model call, no tool round-trip. The media
type is read from the content, so there is one `--attach` and no `--image`/`--pdf`; a bad path fails
the call rather than the run; images only, for now. See `reference/serve.md` for the full field
list.

**If an *agent* needs to decide mid-run what to look at, it needs a skill.** That is the rest of
this guide, and it is still the only route in that case — because the runtime cannot know in advance
what the model will want to see.

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
