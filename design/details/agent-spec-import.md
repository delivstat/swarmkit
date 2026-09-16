---
title: Oracle Open Agent Spec — import, not export
description: A bounded importer that turns an Oracle Agent Spec document (agents, flows, tools) into SwarmKit artifacts a person then governs. No exporter — the lossy direction throws away everything SwarmKit is for, to a spec with one runtime behind it.
tags: [runtime, cli, interop, standards, authoring]
status: proposed
---

# Oracle Open Agent Spec — import, not export

**Scope:** `packages/runtime` (a converter under `authoring/`, a CLI command), reference fixtures
**Design reference:** §5–§6 (topology / agent / archetype / skill), §12 (skills), §7 (principles: topology is data)
**Status:** proposed

## Goal

`swarmkit import agent-spec <file> <workspace>` reads an [Open Agent Specification](https://github.com/oracle/agent-spec)
document (JSON or YAML) and writes SwarmKit artifacts — a topology, archetypes, skills — that
`swarmkit validate` accepts, with every construct the importer could not express written as a
`TODO` the person resolves. Someone who has an agent system described in Agent Spec (or exported
to it from LangGraph, AutoGen or CrewAI via PyAgentSpec) can bring it into SwarmKit in a minute
and then *add* what SwarmKit has and Agent Spec does not: funnels, contracts, role registries,
prerequisites, harness executors, declarative providers.

## Non-goals

- **No exporter.** SwarmKit → Agent Spec would drop funnels, multi-party approval, contracts,
  `requires:`, executor adapters, model providers, IAM scopes and audit configuration — i.e. the
  governance layer that is the product — to produce a document for a 420-star spec with one
  reference runtime (WayFlow) and adapters that are themselves lossy. A portability claim nobody
  has asked for, at the price of a second artifact set to keep in step. Revisit when Agent Spec
  has a second serious runtime or a user asks with a reason.
- **Not a live adapter.** Agent Spec is imported *once* into files a person owns; it does not run
  Agent Spec at runtime and does not re-sync. (Contrast A2A, which is a runtime transport —
  `a2a-interop.md`.)
- **Not round-trip fidelity.** The importer's contract is "valid SwarmKit artifacts plus an honest
  list of what it could not carry", not "the same system".
- **Not a general workflow importer.** Agent Spec *Flows* with branching are mapped where they fit
  SwarmKit's shapes and otherwise emitted as TODOs; the bundled workflow engine was removed in
  1.189.0 on purpose (`extracting-the-pipeline.md`) and a flow importer must not reintroduce one.

## Mapping

Agent Spec (v0.x, `agent_spec_version`) has two runnable component types and a small vocabulary.
The mapping, stated so its edges are visible:

| Agent Spec | SwarmKit | fidelity |
|---|---|---|
| `Agent` (name, system prompt, `llm_config`, `tools[]`) | an **archetype** (`defaults.prompt.system`, `defaults.model`) + an **agent** in the topology bound to it | full |
| `Agent` with `agents[]` (sub-agents) | the topology's `children` hierarchy; the parent gets `role: leader` | full |
| `llm_config` (provider, model, params) | `defaults.model` on the archetype; the provider name is mapped to a bundled `ModelProvider` id when it matches (`openai`, `anthropic`, `ollama`, …) else left as a `${…}` property with a TODO | full when the provider is known |
| `ServerTool` (a function the runtime provides) | a **skill**, `implementation.type: mcp_tool` with a TODO for `server:`/`tool:` — the importer cannot know which MCP server implements it | shape only |
| `ClientTool` (the caller executes it) | a skill of type `llm_prompt` that *describes* the tool and a TODO — SwarmKit has no client-executed tool | TODO |
| `RemoteTool` (HTTP) | a skill of type `mcp_tool` against a generated stdio MCP server *stub* under `<workspace>/mcp-servers/<name>/` that calls the URL, plus the `mcp_servers` entry | usable, reviewed |
| `Flow` with only `AgentNode`s in sequence | a topology whose agents carry `depends_on` in that order | full |
| `Flow` with `AgentNode`s in parallel (no edges between them) | sibling children under a synthesis parent | full |
| `Flow` with `LlmNode` / `ToolNode` | an agent per node with an `llm_prompt` / tool skill; a TODO notes it was a node, not an agent | approximate |
| `Flow` with `BranchingNode` | **not imported** — a TODO block with the branch conditions; a person decides whether it is a decision skill, a funnel layer, or application sequencing (`pipeline-orchestrator` example) | TODO |
| `inputs` / `outputs` | topology `input_schema` / root `output_schema` where they are JSON-schema-shaped | full |
| anything unknown | preserved under `x-agent-spec:` on the nearest artifact so nothing is silently dropped | preserved |

Every TODO is a real `# TODO(agent-spec):` line in the YAML **and** an entry in the import report,
and `swarmkit validate` is run on the result before the command returns, so what comes out is
either valid or says why not.

## API shape

```
swarmkit import agent-spec SPEC_FILE WORKSPACE_DIR
    [--name <topology-id>]        # default: the spec's name, kebab-cased
    [--provider <id>]             # force every llm_config onto one bundled provider
    [--dry-run]                   # print the artifacts and the report, write nothing
    [--overwrite]                 # replace artifacts that already exist (default: refuse)
```

Output:

```
imported research-team (Agent Spec 0.3) into ./my-ws
  topologies/research-team.yaml      1 topology, 4 agents (1 leader, 3 workers)
  archetypes/…                       4 archetypes
  skills/…                           6 skills (3 mcp_tool TODO, 2 remote via stub, 1 llm_prompt)
  mcp-servers/web-search/server.py   generated stub — review before running
  3 TODO(agent-spec) items — see import-report.md
swarmkit validate ./my-ws: ok (3 warnings)
```

Python: `swarmkit_runtime.authoring.agent_spec.import_document(doc: dict, *, name: str | None,
provider: str | None) -> ImportResult(artifacts: dict[Path, str], todos: list[Todo],
preserved: dict[str, Any])` — pure, no I/O, so it is testable on fixture documents and reusable
by the portal later ("Import…" in the composer).

Reading Agent Spec: parse the JSON/YAML directly against the component vocabulary above; **do not
depend on `pyagentspec`** at runtime — it pulls a framework-adapter dependency set for what is,
for us, a document walk. Pin the supported `agent_spec_version` range; a newer major is refused
with a message, not guessed at.

## Test plan

- **Unit — mapping table:** one fixture Agent Spec document per row above, each asserting the
  produced artifact(s) and the TODO list. The fixtures live under
  `packages/runtime/tests/fixtures/agent-spec/` and are also *valid Agent Spec* (checked once
  with `pyagentspec` in a dev-only test that is skipped when it is not installed).
- **Unit — preservation:** an unknown key lands under `x-agent-spec` on the nearest artifact and
  round-trips through `yaml.safe_load`.
- **Unit — refusal:** unsupported major version, a document with no runnable component, name
  collisions without `--overwrite`.
- **Integration:** import Oracle's own examples from the `agent-spec` repo (vendored as fixtures
  with their license) into a temp workspace; `swarmkit validate --tree` passes; the sequential
  flow example runs end-to-end on the mock provider.
- **Schema:** nothing changes in the canonical schemas — the importer emits existing artifact
  kinds only. That is the point.

## Demo plan

`just demo-agent-spec`: imports Oracle's multi-agent example, prints the report, `swarmkit
validate --tree`, then the one-minute "add governance" step — drop a `funnel:` on the root and a
`requires:` on a worker — and re-validate. The blog sentence: "an Agent Spec system, imported, then
given a human gate that the spec has no word for."

## Open questions

- **Which Agent Spec version to target.** The spec is pre-1.0 and its node vocabulary moves; pin
  to the version of the vendored examples and widen when a second runtime stabilises it.
- **Should `RemoteTool` become a first-class skill type** (`implementation.type: http`)? Today it
  is a generated MCP stub. If a second importer (OpenAPI, say) wants the same thing, promote it;
  one importer is not enough reason to add a skill type.
- **The portal path.** The composer already authors artifacts conversationally; "paste an Agent
  Spec" is the same `import_document` behind a button. Not in the first cut.
