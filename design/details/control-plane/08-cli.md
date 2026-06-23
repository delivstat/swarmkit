# 08 — CLI surface

Scope: `packages/runtime/src/swarmkit_runtime/cli/` — Typer app, thin over `WorkspaceRuntime`.
Every command + status below.

| Command | Purpose | Status |
|---|---|---|
| `validate [path]` | validate workspace, print resolved tree (`--json/--tree/--quiet`) | ✅ |
| `knowledge-pack [ws]` | bundle docs+schemas+workspace into an LLM prompt | ✅ |
| `init [path]` | create workspace **conversationally** | ✅ |
| `author topology\|skill\|archetype\|mcp-server [ws]` | conversational authoring (`--thorough` = multi-agent swarm) | ✅ |
| `edit [ws]` | edit workspace conversationally (skill-authoring topology) | ✅ |
| `run <ws> <topology>` | one-shot execute (`--input/--dry-run/--resume/--verbose/--quiet`) | ✅ |
| `eval <ws> <eval-set>` | run eval-set, score (`--compare/--output/--quiet`) | ✅ |
| `chat <ws> <topology>` | interactive multi-turn (`--resume`; `/model`, `/clear` commands) | ✅ |
| `conversations [ws]` | list/resume saved conversations (`--last/--pick`) | ✅ |
| `review list\|show\|approve\|reject` | HITL review queue | ✅ |
| `gaps [ws]` | list recorded skill gaps | ✅ |
| `logs [ws]` | recent run events (`--format text\|markdown` compliance report) | ✅ |
| `status [ws]` | recent run health summary | ✅ |
| `why <run_id>` | LLM explanation of a run | ✅ |
| `ask <question>` | LLM Q&A over workspace + recent runs | ✅ |
| `trace [run_id]` | agent call graph + tokens | ✅ |
| `checkpoints [ws]` | list resumable runs | ✅ |
| `debug [ws]` | inspect local prompt ring buffer (`--span/--run/--agent`) | ✅ |
| `serve [ws]` | HTTP server (`--host 0.0.0.0 --port 8000`) | ✅ |
| `mcp-serve <ws...>` | expose topologies as MCP tools (stdio) | ✅ |
| `knowledge-server` / `docs-reader` | launch built-in MCP servers (stdio) | ✅ |
| `install <pkg>` / `packages` / `publish` | expertise-package lifecycle | ✅ |
| `stop <run_id>` | gracefully stop a run | ⚠️ stub (M6) |
| `eject <topology>` | export generated LangGraph code | ⚠️ stub (M9) |

Exit codes: 0 success / 1 runtime error / 2 usage (incl. stubs).

## Control-plane implications

- **Panel-exposable (remote/UI actions):** run, eval, logs, status, trace, why, ask, review,
  conversations, the artifact CRUD (via serve `/api/*`), install/packages/publish (distribution).
  These map to serve routes ([02](02-serve-api.md)) or aggregate cleanly.
- **Local-dev-only:** `init`, `author *`, `edit` (conversational authoring is user-local today),
  `validate`, `knowledge-pack`, `debug` (local prompts), `eject`. The conversational authoring is
  the surface the panel should eventually front (the UI's "missing" surface — [09](09-ui.md)).
- **Two stubs** (`stop`, `eject`) — `eject` being unimplemented blocks the "export the policy as
  code" deferral noted elsewhere; `stop` matters for a panel "cancel run" action (needs M6).
- The CLI is a faithful catalog of *what the platform can do*; the panel is largely "these
  capabilities, multi-instance + authenticated + aggregated."
