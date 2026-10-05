# swarmkit author — first-run findings

Headline numbers in `report.md` show 9/10 "pass" against `swarmkit validate`. That is the floor, not the ceiling: validate catches shape, not intent. Below is what the spot-check turned up.

## What works

- **topology/uses-existing-skill** — correctly created a `greeter-worker` archetype that *references* the pre-seeded `say-hello` skill (not a hallucinated clone). This is a real win: the author read the workspace fixture and composed against it.
- **mcp-server/stdio-filesystem** — edited `workspace.yaml` to add the correct `mcp_servers` block with the right command and transport. Minimal + correct.
- **skill/mcp-tool-wrap** — produced a well-shaped mcp_tool skill against the pre-seeded `docs-search` server.
- **archetype/decision-role** and **archetype/curator-worker** — both compiled, both referenced existing artefacts correctly.
- **init mode is missing from the CLI** — in `AuthoringMode` literal but no `swarmkit author init` subcommand ships. Separate bug.

## Quality misses that still passed validate

### topology/triage-with-funnel — the Funnel was never actually created

The prompt was explicit: *"A human has to approve before the refund is returned."* That is textbook Funnel territory ([memory: project_structured_output_governance]: funnels own the human-approval layer).

What the author produced: five artefacts (archetype, three skills, topology) plus prose in the system prompt saying *"this will be reviewed by a human before any refund is issued."* **No `Funnel` kind anywhere.** The topology passes `swarmkit validate` because the schema does not require a funnel; the human review is a comment the model wrote in prose.

Why it matters: Funnels are a load-bearing SwarmKit primitive for human-in-the-loop. If the authoring agent routes around them to prose, the whole gating story breaks silently.

### topology/triage-with-funnel — provider was overridden to `groq/llama-3.3-70b-versatile`

Prompt said `Use the mock provider`. Author picked groq. Minor, but the authoring prompt should respect provider directives.

### mcp-server/http-remote — workspace.yaml unchanged

Prompt asked for a `notion-remote` HTTP MCP server with a bearer-token credential. The author ran for 36s, exited cleanly, declared nothing. The run looks like a pass only because the harness permits `files_created_min: 0` for mcp-server mode. **Real result: nothing shipped.**

### topology/cold-brief — no `output_schema`

Prompt said *"returns … 3 items + a short follow-up question"*. The ideal topology declares an `output_schema` describing that shape. Author produced a free-text prompt instead. Validates, but every downstream consumer has to parse prose.

## Harness adjustments the next run should make

- Count a workspace.yaml *edit* as output (currently only top-level dirs are tracked). Then http-remote fails the way it should.
- Add structural checks per mode:
  - topology: has `output_schema` on root agent when the requirement implies structured output
  - topology/skill/archetype: no hallucinated references (every `skills:` entry resolves)
  - When the requirement names a provider, that provider is the one used
  - When the requirement asks for human approval, there is a `Funnel` artefact
- Add retry-until-pass metric (how many attempts to generate a correct artefact).

## Implication for #1040

The delegation design is **basically sound** — the author produces schema-valid artefacts, references pre-existing skills, edits workspace files. For simple topology/skill/archetype generation we can rely on it today.

The **quality bar is lower than #1040 assumed**: the Dot Author's archetype currently says *"delegate to `swarmkit author`"* without disclaimers. We should either (a) land charter fixes in `swarmkit author`'s per-mode prompts to close the Funnel-miss / schema-miss / provider-respect gaps, or (b) have the Dot Author inspect what came back and prompt the user to confirm before writing the Dot entry.

Not blocking #1040; the Dot wrapper itself still works. But the delegation UX is weaker than the PR implied.

## Next bench pass (benchmark v2 — see #1041)

- Add an LLM judge rubric covering the quality gaps above.
- Run the same corpus against Claude Sonnet and GPT-4o-mini for provider comparison.
- Run topology/skill/archetype under `--thorough` to see if the multi-agent swarm closes the quality gaps.
- Fix the `init` CLI subcommand so cold-start can be measured.
