# Changelog

Every released version of the SwarmKit runtime, newest first, generated from the annotated git tags (`v1.0.0` – present). Each version is a runtime + schema bump; see the [GitHub releases](https://github.com/delivstat/swarmkit/releases) and [tags](https://github.com/delivstat/swarmkit/tags) for the full diff.

The earlier per-series notes ([v1.2](v1.2.md), [v1.1](v1.1.md)) carry hand-written detail for those ranges.

## July 2026

- **v1.98.0** (2026-07-20) — artifact env-substitution: ${VAR}/${VAR:-default}/$${VAR} resolved across all artifact YAML, with or without a workspace env file
- **v1.97.0** (2026-07-19) — multi-party approval (governance): role registry + approval-policy schemas, evaluation engine, and runtime gate resolution
- **v1.96.0** (2026-07-13) — serve hosts the web portal (swarmkit-runtime[ui] + swarmkit-webui)
- **v1.95.0** (2026-07-13) — topology canvas examine mode (run overlaid on the graph); get_job returns topology
- **v1.94.0** (2026-07-13) — gatewayed MCP for harnesses (container-reachable); harness feature complete
- **v1.93.0** (2026-07-13) — ephemeral governed MCP gateway for harnesses (native)
- **v1.92.0** (2026-07-13) — extract the one governed MCP-call path
- **v1.91.1** (2026-07-13) — container sandbox demo + close-out (feature complete)
- **v1.91.0** (2026-07-13) — container sandbox mounts + MCP reachability
- **v1.90.0** (2026-07-13) — build-in-sandbox (no local harness install)
- **v1.89.0** (2026-07-13) — enforced container egress (deny + allowlist proxy)
- **v1.88.0** (2026-07-13) — container sandbox provisioner (docker|podman)
- **v1.87.0** (2026-07-12) — harness executor + container sandbox docs; knowledge pack includes guides
- **v1.85.0** (2026-07-12) — trust accrual: repeated relay approvals propose an allowlist changeset (P3.5)
- **v1.84.0** (2026-07-12) — harness-gate review surface: CLI + HTTP API + serve UI + fleet UI
- **v1.83.0** (2026-07-12) — §6.3 input-request escalation (classifier + human inbox + memoize)
- **v1.81.0** (2026-07-12) — executor relay: park-resume mid-run permission approvals (verified e2e)
- **v1.78.0** (2026-07-12) — executor P3 complete: launch review gate + adapter authoring guide
- **v1.77.0** (2026-07-12) — executor P3: declarative harness adapters (harnesses are data)
- **v1.69.0** (2026-07-11) — executor abstraction P2 complete (harness executors, end to end)
- **v1.60.1** (2026-07-11) — Publish swarmkit-schema 1.11.0 (x-swarmkit-ref reference hints).
- **v1.60.0** (2026-07-11) — GET /api/schema/{artifact_type} — canonical schema for the designer
- **v1.59.0** (2026-07-11) — maintenance release
- **v1.58.0** (2026-07-10) — observability reads — GET /observability/runs/{id}/trace + /audit
- **v1.57.0** (2026-07-10) — GET /auth-info — serve advertises its auth mode
- **v1.56.0** (2026-07-10) — emit governance-decision + approval-wait metrics + dashboard panels
- **v1.55.0** (2026-07-10) — wire OTLP metrics export so Grafana populates
- **v1.54.0** (2026-07-10) — scenario severity + VLM escalation (local vs cloud) for Scenario Studio

## June 2026

- **v1.8.0** (2026-06-22) — context compression (lossless columnar + reversible headtail + plugin backends), per-surface policy, context_retrieve, observability, per…
- **v1.3.6** (2026-06-13) — generic per-model options passthrough (schema 1.3.3, runtime 1.3.6)
- **v1.3.5** (2026-06-13) — schema-constrained output decoding from output_schema
- **v1.3.4** (2026-06-13) — surface structured MCP tool output when no text block
- **v1.3.3** (2026-06-10) — Gemma/Google model tool-calling fixes
- **v1.3.2** (2026-06-06) — GBrainMemory compiler wiring
- **v1.3.1** (2026-06-03) — tool call tracing + UI trace panel
- **v1.3.0** (2026-06-01) — M11 launch prep + expertise packages

## May 2026

- **v1.2.70** (2026-05-31) — Topology Composer (3 views, CRUD, YAML editing), cost optimization (dual model, token tracking)
- **v1.2.69** (2026-05-31) — dual model, token tracking, vedanta optimizations
- **v1.2.68** (2026-05-31) — dual model (tool/synthesis split), accurate token tracking
- **v1.2.67** (2026-05-31) — schema version bump for PyPI
- **v1.2.66** (2026-05-31) — accurate token tracking, CRUD endpoints, composer scaffold, configurable store, vedanta scripture fixes
- **v1.2.63** (2026-05-28) — chat UI polish, real-time progress, markdown, retry, token tracking
- **v1.2.62** (2026-05-28) — UI dashboard + chat, SQLite persistence, workspace memory, canary deployments
- **v1.2.60** (2026-05-28) — schema version bump for PyPI publish
- **v1.2.59** (2026-05-28) — workspace memory (GBrain + local), canary deployments, UI dashboard, serve triggers
- **v1.2.49** (2026-05-19) — maintenance release
- **v1.2.47** (2026-05-19) — fix single-agent tool stripping + add all MCP tools to archetype
- **v1.2.46** (2026-05-19) — architect solution reasoning in scope + mermaid syntax rules
- **v1.2.45** (2026-05-19) — topology-level synthesis prompt config + Sterling HLD prompt
- **v1.2.44** (2026-05-19) — fix synthesizer file write: natural language template extraction + default output path
- **v1.2.43** (2026-05-19) — synthesizer trace + openrouter provider + version bump
- **v1.2.42** (2026-05-19) — synthesizer + writing tool gating + schema update
- **v1.2.41** (2026-05-19) — add synthesis field to workspace schema
- **v1.2.40** (2026-05-19) — automatic synthesizer: single large-context call for document generation
- **v1.2.39** (2026-05-19) — compiler-driven two-phase execution with tool gating
- **v1.2.38** (2026-05-19) — fix OpenAI tool_calls message format for Qwen/Groq/Together
- **v1.2.37** (2026-05-18) — timeout + retry with jitter on all model provider calls
- **v1.2.36** (2026-05-18) — fix resume fast-path: root skips to architect on task plan resume
- **v1.2.35** (2026-05-18) — fix resume routing to architect on crashed run recovery
- **v1.2.34** (2026-05-18) — fix trace recursion + differentiated configurable tool limits
- **v1.2.33** (2026-05-18) — fix forced synthesis dead code, tool loop guards, docs-researcher redesign
- **v1.2.32** (2026-05-18) — Wave 3 complete + gate-validator MCP server
- **v1.2.31** (2026-05-18) — Wave 2 complete: deterministic grounding + Rynko Flow integration
- **v1.2.29** (2026-05-18) — structured checkpoint instructions (JSON action specs)
- **v1.2.28** (2026-05-18) — auto-populate source fields from tool provenance
- **v1.2.27** (2026-05-18) — MCP provenance envelope on every tool call
- **v1.2.26** (2026-05-18) — default output_schema for workers (structured inter-agent communication)
- **v1.2.25** (2026-05-18) — planning config (scope_required + two_phase) as workspace/topology settings
- **v1.2.24** (2026-05-18) — create-scope + update-scope + read-scope tools
- **v1.2.23** (2026-05-17) — check for state-changing tools after nudge response
- **v1.2.22** (2026-05-16) — tool loop detects update-task-plan for two-phase execution
- **v1.2.21** (2026-05-16) — two-pass task plan handler for correct two-phase execution
- **v1.2.19** (2026-05-16) — handle update-task-plan + freeze-scope feedback in tool loop
- **v1.2.18** (2026-05-16) — Phase 1 checkpoint prompts freeze-scope + update-task-plan
- **v1.2.17** (2026-05-16) — fix auto-synthesize for single-task Phase 1 plans
- **v1.2.16** (2026-05-16) — fix freeze-scope tool loop handling
- **v1.2.15** (2026-05-16) — freeze-scope platform tool + spec-conformance decision skill
- **v1.2.14** (2026-05-16) — sync bundled schemas for decision_skills validation
- **v1.2.13** (2026-05-16) — governance decision skill evaluator, retry loop, Sterling grounding
- **v1.2.12** (2026-05-16) — governance decision skills with workspace/topology merge
- **v1.2.11** (2026-05-16) — forced synthesis forbids planning language
- **v1.2.10** (2026-05-16) — planning text filter, jira-researcher upgrade, strip narration from history
- **v1.2.9** (2026-05-16) — archive run-state after completion for history
- **v1.2.8** (2026-05-15) — swarmkit why reads trace files and task plans
- **v1.2.7** (2026-05-15) — trace hierarchy fix for task-executed children + Sterling README update
- **v1.2.6** (2026-05-15) — fix read-task-result re-delegation loop
- **v1.2.5** (2026-05-15) — nudge on empty response for delegation agents
- **v1.2.4** (2026-05-15) — auto-add synthesis task when model omits it
- **v1.2.3** (2026-05-15) — auto-fix missing depends_on for synthesis tasks
- **v1.2.2** (2026-05-15) — enforce depends_on in task plans, fix flat plan bug
- **v1.2.1** (2026-05-15) — structured delegation PRs 2-5: execution engine, checkpoint review, summaries, resume from disk
- **v1.2.0** (2026-05-15) — structured delegation (task plan + tool injection) + compiler split into 8 modules
- **v1.1.18** (2026-05-15) — checkpoint visibility, resume on error, checkpoints CLI
- **v1.1.17** (2026-05-15) — delegation cap per child, workers on deepseek-v4-flash
- **v1.1.16** (2026-05-14) — trim developer to 17 skills, fix pandoc tool name
- **v1.1.15** (2026-05-14) — partial child results, tighter archetype prompts
- **v1.1.14** (2026-05-14) — tool result details in progress output
- **v1.1.13** (2026-05-14) — fix parent re-invocation during child delegation
- **v1.1.12** (2026-05-14) — user-facing progress output during execution
- **v1.1.11** (2026-05-14) — fix re-delegation loop on tool limit, bump default to 50 turns
- **v1.1.10** (2026-05-14) — trace parent tracking
- **v1.1.9** (2026-05-14) — run trace with agent call graph and token tracking
- **v1.1.8** (2026-05-14) — invalid self-delegation fix + all-children-done stripping
- **v1.1.7** (2026-05-14) — coordinator nudge fix
- **v1.1.6** (2026-05-14) — dynamic recursion limit
- **v1.1.5** (2026-05-14) — sequential delegation + MCP timeout/retry
- **v1.1.4** (2026-05-14) — sequential delegation fix
- **v1.1.3** (2026-05-13) — forced synthesis fix
- **v1.1.2** (2026-05-13) — forced synthesis at tool limit
- **v1.1.1** (2026-05-13) — cache fix + MarkItDown CWD
- **v1.1.0** (2026-05-13) — M8 MCP integration layer
- **v1.0.36** (2026-05-08) — DAG dependency graph, Rynko content pipeline
- **v1.0.35** (2026-05-05) — parallel delegation, image fallback, path fix
- **v1.0.34** (2026-05-05) — image fallback + path sanitisation fix
- **v1.0.33** (2026-05-05) — cache error fix, Confluence page/image workflow
- **v1.0.32** (2026-05-05) — tool cache, history compaction, /clear
- **v1.0.31** (2026-05-03) — ASCII banner + suppress noisy MCP logs
- **v1.0.30** (2026-05-03) — DRY refactor, code quality fixes, authoring UX
- **v1.0.29** (2026-05-03) — prompt_toolkit for author, AIMessage fix, incomplete nudge
- **v1.0.28** (2026-05-03) — fix AIMessage/Message type mismatch in retry
- **v1.0.27** (2026-05-03) — nudge incomplete tool loop responses
- **v1.0.26** (2026-05-03) — conversation context for worker agents
- **v1.0.25** (2026-05-03) — enforce code-first workflow, 8-turn tool limit
- **v1.0.24** (2026-05-03) — multi-turn tool loop
- **v1.0.23** (2026-05-03) — tool result synthesis, smart path sanitisation, relative grep
- **v1.0.22** (2026-05-03) — smart path sanitisation, relative grep paths
- **v1.0.21** (2026-05-02) — fix embedded env var expansion in MCP servers, master ingestion script
- **v1.0.20** (2026-05-02) — agentic retry loop for tool use
- **v1.0.19** (2026-05-02) — maintenance release
- **v1.0.18** (2026-05-02) — prompt_toolkit chat UX, CDT config server
- **v1.0.17** (2026-05-02) — MCP server cwd support with schema
- **v1.0.16** (2026-05-02) — MCP server cwd support
- **v1.0.15** (2026-05-02) — fix filesystem MCP server cwd
- **v1.0.14** (2026-05-02) — debug release
- **v1.0.13** (2026-05-02) — maintenance release
- **v1.0.12** (2026-05-02) — multi-tool execution, path sanitisation
- **v1.0.11** (2026-05-02) — multi-tool execution, /model command, DeepSeek workers
- **v1.0.10** (2026-05-02) — /model chat command, verbose logging, forced delegation
- **v1.0.9** (2026-05-01) — verbose agent logging, forced delegation, custom MCP servers
- **v1.0.8** (2026-05-01) — ChromaDB batch ingestion, FTS5 search, Graphify wrapper, PDF/DOCX/Excel support
- **v1.0.7** (2026-05-01) — MCP session persistence, command arg expansion, newline-delimited JSON protocol

## April 2026

- **v1.0.6** (2026-04-29) — graceful MCP server failure handling
- **v1.0.5** (2026-04-29) — lazy provider imports, Sterling workspace improvements
- **v1.0.4** (2026-04-28) — multi-turn chat, Sterling workspace, observability prompts
- **v1.0.3** (2026-04-27) — fix swarmkit init prompt formatting
- **v1.0.2** (2026-04-27) — dry-run, markdown audit, annotations
- **v1.0.1** (2026-04-26) — observability
- **v1.0.0** (2026-04-26) — launch prep — skills catalogue, docs site, Docker, PyPI, CHANGELOG

