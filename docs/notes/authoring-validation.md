# Validating the authoring surface

The authoring migration (#1045) ships across CLI, serve REST, serve AG-UI and the
portal UI. The code paths are shared but each one needs a slightly different setup to
reproduce. This note captures the three validation passes we run before any change to
the bundled `swarmkit:author:*` topologies or the IAM-scoped command_pack scripts, so
the next person doesn't have to rediscover them.

## 1. Portal screenshots

Needed on every PR that touches `packages/ui/app/author/`, `packages/ui/app/chat/`
(deep-link behaviour), or the sidebar nav. Attached to the PR body per
`packages/ui/CLAUDE.md` ("every UI PR attaches screenshots of every route and every
action").

**Seed workspace** (any throwaway path; opts in to authoring):

```bash
mkdir -p /tmp/swarmkit-author-demo
cat > /tmp/swarmkit-author-demo/workspace.yaml <<'EOF'
apiVersion: swarmkit/v1
kind: Workspace
metadata:
  id: author-demo
  name: Author demo
authoring:
  expose: true
EOF
```

**Serve + dev** (two terminals, or two backgrounded commands):

```bash
# A: serve
swarmkit serve /tmp/swarmkit-author-demo --port 8099 --insecure \
    --cors-origin http://127.0.0.1:3009

# B: Next dev pointed at it
NEXT_PUBLIC_SWARMKIT_API=http://127.0.0.1:8099 \
    pnpm --filter @swarmkit/ui dev -p 3009
```

**Capture** (Playwright script committed under `packages/ui/demos/`):

```bash
pnpm --filter @swarmkit/ui demo:author /tmp/author-shots
# writes sidebar-author.png, author-modes.png, chat-after-handoff.png
```

Attach those three PNGs to the PR body. Follow the pattern established by
`demo:approval`, `demo:tutorial`, etc. — one Playwright script per shippable surface,
one pnpm script alias, output goes to a caller-chosen directory.

**Reference captures** (from the first run of this process, 2026-10-06):

- `img/sidebar-author.png` — Dashboard with the Author entry between Chat and
  Composer. The run history already shows CLI author sessions grouped as
  `swarmkit:author:topology · 2 runs`.
- `img/author-modes.png` — the five mode cards on `/author` when
  `authoring.expose: true`. Composer link in the subtitle for hand-edit.
- `img/chat-after-handoff.png` — click Topology → `/chat?conversation=<id>` opens the
  fresh authoring conversation at the top of the sidebar.

## 2. CLI multi-turn smoke test

Needed on every change to `authoring/_prompts.py`, the bundled topology YAMLs under
`authoring_workspace/`, the command_pack scripts, or `cli/_cmd_authoring.py`'s REPL.

This exercises the thing one-shot `swarmkit run` can't: the author's propose-plan →
user-approve → write-file cycle. The REPL threads `thread_id` through consecutive
`WorkspaceRuntime.run` calls, so turn N sees everything from turns 1..N-1 (serve chat
uses the exact same mechanism — same code path, same `correlation_id = thread_id`
convention).

**Minimum test** (needs `OPENROUTER_API_KEY` in env):

```bash
mkdir -p /tmp/author-smoke/topologies
cat > /tmp/author-smoke/workspace.yaml <<'EOF'
apiVersion: swarmkit/v1
kind: Workspace
metadata:
  id: author-smoke
  name: Author smoke
EOF

cat <<EOF | swarmkit author topology /tmp/author-smoke
A topology that summarises a URL in exactly 3 bullets. Call it url-brief.
yes, do it
Actually make it 5 bullets instead of 3, same shape otherwise.
yes, do it
/exit
EOF
```

**Pass criteria:**

1. `/tmp/author-smoke/topologies/url-brief.yaml` exists after turn 2.
2. Its `output_schema.properties.items.maxItems` is `5`, not `3` — proves the
   checkpointer resumed and the agent kept the first-turn context.
3. One correlation id groups both turns in `GET /jobs/history` (visible via the
   portal Dashboard as `swarmkit:author:topology · 2 runs` with the same
   `correlation_id`).

If turn 2 produces a 3-bullet topology, the resume isn't working.

### One-shot does not test this

`swarmkit run <ws> swarmkit:author:topology --input "<req>"` is a single turn.
The author's charter proposes a plan and waits for approval before calling
`write-file` — one turn is not enough. The bench harness (`run.py`) hits the same
limitation on purpose: it measures the one-shot code path, not the multi-turn chat
UX. See §3.

## 3. Bench rerun

Needed when the bundled authoring prompts or the command_pack scripts change in a
way that could affect quality.

**Run** (needs `OPENROUTER_API_KEY`):

```bash
uv run python packages/runtime/benches/author/run.py
# writes packages/runtime/benches/author/results/report.md + per-case transcripts
```

The harness runs each case as `swarmkit run <ws> swarmkit:author:<mode> --input "<req>"`
— the one-shot path. Expect low pass rates for modes whose charter asks the user to
approve a plan before writing (topology, skill, archetype, init). Those cases
reliably exit 0 with 0 files; the agent proposes a design and stops. That's the
one-shot limit, not a regression.

What the bench actually measures:

- **mcp-server**: typically writes workspace.yaml changes on the first turn, so file
  counts look good.
- Everything else: the agent produces a plan in prose; files only land after a
  user-approval turn the bench doesn't deliver.

**For a true multi-turn bench** we'd need the harness to script approval turns
through the REPL path — tracked as a follow-up (not shipped). The one-shot numbers
are still useful as a floor-regression signal: if a case that USED to produce files
on turn 1 stops doing so after a prompt change, that's a signal to look.

**Record the report** (per `feedback_record_benchmarks`): write-up goes to the PR
body with model, options, case count, git sha and host. Reports under
`packages/runtime/benches/author/results/` are gitignored — attach them to the PR
instead of committing.

## Known gotchas

- **Serve binds 0.0.0.0 by default**. `--port 8099 --insecure` is fine for a local
  demo; don't do this on a shared host.
- **Playwright must wait on `domcontentloaded`**, not `networkidle`, for a fresh Next
  dev server — the Fast Refresh HMR socket keeps the network bus busy and `networkidle`
  times out even when the page is interactive. Use a 60s selector timeout as a floor.
- **The CLI REPL's `input()` blocks**. Piping multiple turns in via stdin works, but
  the author's charter asks for approval between turns, so remember to script the
  "yes" lines too (or the REPL exits before the write).
- **Topology ids are case-sensitive and namespaced**. Pass `swarmkit:author:topology`
  verbatim — colons are only allowed on bundled ids; a user workspace can't register
  anything under the `swarmkit:` namespace (schema refuses it).
