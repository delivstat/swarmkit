# Changelog

All notable changes to Swael are documented here.

## [1.0.0] — 2026-04-26

### The first release

Swael v1.0 ships a complete framework for composing, running, and
growing multi-agent AI swarms. Topology-as-data, skills-as-extension,
governance-built-in.

### Runtime

- **CLI:** `validate`, `run` (with `--verbose`), `init`, `author`
  (with `--thorough`), `edit`, `serve`, `status`, `logs`, `why`,
  `ask`, `knowledge-pack`, `knowledge-server`, `review`, `gaps`
- **HTTP server:** `swael serve` — FastAPI wrapping WorkspaceRuntime
- **LangGraph compiler:** topology YAML → executable StateGraph with
  arbitrary-depth agent trees, delegation, skill dispatch
- **7 model providers:** Anthropic, Google, OpenAI, OpenRouter, Groq,
  Together, Ollama — auto-detected from environment
- **Governance:** AGT-backed policy enforcement, identity verification,
  hash-chained audit. Mock provider for development
- **MCP integration:** stdio + HTTP transports, workspace-level server
  registry, `inputSchema` forwarding, compile-time server validation,
  Docker sandbox isolation with per-server image
- **Knowledge MCP Server:** 10 tools — search_docs, get_schema,
  list_schemas, get_design_note, list_design_notes,
  list_reference_skills, validate_workspace, get_error_reference,
  read_workspace_file, write_workspace_file, run_pytest
- **Structured output:** schema-constrained generation + field-specific
  auto-correction on validation failure
- **Decision skills:** LLM judges with verdict + confidence + reasoning
- **Review queue + skill gap log:** HITL primitives with CLI commands
- **Conversational authoring:** single-agent (quick) and multi-agent
  swarm (thorough) paths, MCP-first guidance
- **Observability:** structured run events (agent timing, skill calls,
  policy denials, validation failures) saved as JSONL. CLI: `status`
  (run history), `logs` (detailed events), `why` (LLM-powered
  explanation), `ask` (conversational observer). Per-skill `audit:`
  block for privacy/compliance control (log_inputs/outputs/redact)

### Schema

- **5 canonical JSON Schemas:** topology, skill, archetype, workspace,
  trigger — dual-language validators (Python + TypeScript)
- **Codegen:** pydantic models + TypeScript types generated from schemas,
  drift-protected in CI
- **Workspace schema:** governance, credentials, mcp_servers (with env,
  sandbox_image), storage

### Reference

- **2 topologies:** Code Review Swarm (3-leader, 10-agent),
  Skill Authoring Swarm (6-agent)
- **16 archetypes:** leaders, code review workers, authoring agents
- **20 skills:** GitHub MCP (3), decision (6), knowledge (7),
  coordination (1), persistence (1), capability templates (2)

### Infrastructure

- **CI:** GitHub Actions — Python 3.11/3.12/3.13, TypeScript, schema
  codegen drift, JSON Schema validity
- **Docker:** runtime image + MCP sandbox image
- **Docs:** GitHub Pages via MkDocs Material
