---
title: Conversational authoring — swael init / swael author (M3.5)
description: Interactive single-agent authoring that generates workspace artifacts through conversation. Users never write YAML.
tags: [cli, authoring, m3.5]
status: proposed
---

# Conversational authoring (M3.5)

## Goal

Users describe what they want in natural language. The authoring agent
asks clarifying questions, generates YAML artifacts, validates them in
real-time, and writes files on approval. **The user never writes YAML.**

This is the primary user interface for Swael. Moving it from M7-M8
to M3.5 reflects that: conversational authoring is the product, not a
late-stage feature.

## What this is NOT

- **Not a multi-agent authoring swarm.** The design doc (§12) describes
  a Review Leader + Schema Expert + Test Runner swarm for skill
  authoring. That's M7-M8. This is a **single conversational agent**
  with the Swael knowledge corpus as context. Simpler, shippable now.
- **Not MCP-dependent.** The authoring agent generates YAML and calls
  `swael validate`. No external tool servers needed.
- **Not a one-shot generator.** The agent asks questions, proposes,
  iterates. It's a conversation, not a prompt → output pipe.

## CLI entry points

```bash
# Create a new workspace from scratch
swael init

# Author a specific artifact type in an existing workspace
swael author topology [name]
swael author skill [name]
swael author archetype [name]
```

All four run the same authoring agent with different initial context.

## Conversation flow

### `swael init` (new workspace)

```
$ swael init
Swael workspace authoring — let's build your swarm.

What will this swarm do?
> Review pull requests for our Python codebase

Got it — a code review swarm. A few questions:

1. Should it check just code quality, or also security and performance?
> Quality and security, not performance

2. Do you want a single reviewer or multiple specialists?
> Two specialists — one for code quality, one for security

3. Which model should the agents use?
> Claude for the supervisor, Gemini for the workers

Here's what I'll create:

  workspace.yaml        — id: code-review, name: Code Review Swarm
  topologies/review.yaml — root supervisor + 2 worker agents
  archetypes/
    quality-reviewer.yaml  — code quality specialist
    security-reviewer.yaml — security specialist
  skills/
    code-quality-check.yaml
    security-scan.yaml

Create these files? [Y/n]
> y

✓ Workspace created at ./code-review/
  Run: swael validate ./code-review/
```

### `swael author skill` (add to existing workspace)

```
$ swael author skill
What should this skill do?
> Check if a Python function has type hints on all parameters

What category is this? (capability / decision / coordination / persistence)
> decision — it returns pass/fail

What does the output look like?
> pass or fail, with a list of untyped parameters

I'll create:

  skills/type-hint-check.yaml
    category: decision
    outputs:
      verdict: enum [pass, fail]
      untyped_params: array of strings

Create this file? [Y/n]
```

## Architecture

### The authoring agent

A single LLM call loop:

```
while not done:
    user_message = input()
    response = model.complete(
        system=AUTHORING_SYSTEM_PROMPT,
        messages=conversation_history,
        tools=[validate_yaml, write_files, read_workspace],
    )
    if response has tool_use:
        execute tool, add result to conversation
    else:
        print response, add to conversation
```

### Tools available to the authoring agent

| Tool | What it does |
|---|---|
| `validate_yaml` | Runs `resolve_workspace` on the generated YAML, returns errors or "valid" |
| `write_files` | Writes generated YAML to disk (requires user confirmation) |
| `read_workspace` | Reads existing workspace files (for `swael author` in an existing workspace) |
| `list_schemas` | Returns the JSON Schema for the artifact type being authored |

### System prompt

The authoring agent's system prompt includes:

1. Swael's core concepts (topology, agents, skills, archetypes)
2. The JSON Schema for each artifact type (so it generates valid YAML)
3. The hello-swarm example as a reference
4. Instructions to ask clarifying questions, not assume
5. Instructions to validate after generating, fix errors conversationally

The knowledge pack (`swael knowledge-pack`) provides the corpus.
The system prompt is a focused subset — just what the agent needs for
authoring.

### Provider for the authoring agent

The authoring agent itself needs a model. Resolution:

1. `SWARMKIT_AUTHOR_MODEL` env var (e.g. `google/gemini-2.5-flash`)
2. `SWARMKIT_PROVIDER` + `SWARMKIT_MODEL` env vars
3. Fall back to the first available real provider in the registry
4. Error if no provider is available (authoring needs a real model)

### User confirmation before writes

The agent generates YAML in the conversation. When the user approves,
the agent calls `write_files`. The tool implementation:

1. Shows the user exactly what files will be written
2. Waits for explicit confirmation (`[Y/n]`)
3. Writes files
4. Runs `swael validate` on the result
5. Reports success or errors

No files are written without the user saying yes. This is the human
approval gate from design §8.7 applied to authoring.

## Implementation

### New module: `packages/runtime/src/swael_runtime/authoring/`

```
authoring/
├── __init__.py          # run_authoring_session() entry point
├── _agent.py            # The authoring agent loop
├── _tools.py            # validate_yaml, write_files, read_workspace, list_schemas
└── _prompts.py          # System prompts for each authoring mode
```

### CLI wiring

Replace the `init` and `author *` stubs with real implementations that
call `run_authoring_session()`. The session runs in the terminal —
reads from stdin, prints to stdout, interactive.

## Test plan

- **Unit: prompt construction.** Given authoring mode + existing
  workspace state, system prompt includes the right schemas and context.
- **Unit: validate_yaml tool.** Given valid YAML → returns "valid".
  Given invalid YAML → returns the error with suggestion.
- **Unit: write_files tool.** Writes to tmp directory, validates
  result.
- **Integration: init session with mock model.** Mock model returns a
  scripted conversation (ask question → user answers → generate YAML →
  validate → write). Assert files written match expected shape.
- **Integration: author skill in existing workspace.** Mock model
  generates a skill YAML that references existing archetypes. Assert
  validation passes.

## Demo

`just demo-authoring` runs a scripted `swael init` session using
mock model responses, showing the full flow: questions → answers →
YAML generation → validation → file write.

## Exit demo

A user runs `swael init`, answers 3-4 questions, and gets a working
workspace. `swael validate` on the result passes. `swael run`
on the result produces output (with mock or real providers).

## Relationship to M7-M8

M7 (Skill Authoring Swarm) and M8 (Workspace Authoring Swarm) add:
- Multi-agent authoring (Review Leader reviews the generated artifact)
- Automated testing (Test Runner executes the authored skill)
- Publication workflow (pending-review → human approval → active)

M3.5 is the single-agent foundation. M7-M8 upgrade it to a governed
multi-agent flow. The CLI entry points (`swael init`,
`swael author`) stay the same — the implementation behind them
becomes more sophisticated.
