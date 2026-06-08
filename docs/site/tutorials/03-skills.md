# Level 3: Skills

Give your agents capabilities — tools they can call, decisions they can make, and actions they can take.

## What you'll learn

- Four skill categories (capability, decision, coordination, persistence)
- Three implementation types (mcp_tool, llm_prompt, composed)
- Binding skills to archetypes
- Input/output schemas
- Retry and failure handling

## Why skills?

Without skills, agents can only generate text. With skills, they can read files, call APIs, validate outputs, search databases, and coordinate with each other. Skills are SwarmKit's only extension primitive — when you need custom behavior, you write a skill.

## Build it

### 1. A capability skill (MCP tool)

Capability skills do things — read data, call APIs, write files. Most use MCP servers:

```bash
mkdir skills
```

```yaml
# skills/read-file.yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: read-file
  name: Read File
  description: Read the contents of a file from the workspace.
category: capability
implementation:
  type: mcp_tool
  server: filesystem
  tool: read_file
input_schema:
  type: object
  required: [path]
  properties:
    path:
      type: string
      description: Path to the file to read.
provenance:
  authored_by: human
  version: 1.0.0
```

This skill calls the `read_file` tool on a `filesystem` MCP server. We'll configure the MCP server in Level 5 — for now, let's focus on the skill definition.

### 2. A decision skill (LLM judge)

Decision skills evaluate something and return a verdict — pass, fail, or needs-review:

```yaml
# skills/quality-check.yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: quality-check
  name: Quality Check
  description: >
    Evaluates whether a response is clear, accurate, and
    complete. Returns pass/fail with reasoning.
category: decision
implementation:
  type: llm_prompt
  prompt: |
    Evaluate the following response for quality:

    RESPONSE:
    {{input}}

    Score on three criteria:
    1. Clarity — is it easy to understand?
    2. Accuracy — is the information correct?
    3. Completeness — does it fully answer the question?

    Return JSON:
    {
      "verdict": "pass" or "fail",
      "reasoning": "why you gave this verdict",
      "scores": {"clarity": 1-5, "accuracy": 1-5, "completeness": 1-5}
    }
output_schema:
  type: object
  required: [verdict, reasoning]
  properties:
    verdict:
      type: string
      enum: [pass, fail, needs-review]
    reasoning:
      type: string
provenance:
  authored_by: human
  version: 1.0.0
```

Decision skills MUST have a `reasoning` field in their output — this is enforced by the schema.

### 3. An LLM prompt skill

Not all skills call tools — some are just structured LLM prompts:

```yaml
# skills/summarize.yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: summarize
  name: Summarize
  description: Summarize text into bullet points.
category: capability
implementation:
  type: llm_prompt
  prompt: |
    Summarize the following text into 3-5 bullet points.
    Each bullet should be one clear sentence.

    TEXT:
    {{input}}
provenance:
  authored_by: human
  version: 1.0.0
```

### 4. Bind skills to an archetype

Update your archetype to include skills:

```yaml
# archetypes/friendly-assistant.yaml — updated
apiVersion: swarmkit/v1
kind: Archetype
metadata:
  id: friendly-assistant
  name: Friendly Assistant
  description: A helpful assistant with file reading and summarization.
role: worker
defaults:
  model:
    provider: openrouter
    name: meta-llama/llama-3.3-70b-instruct
    temperature: 0.7
    max_tokens: 2048
  prompt:
    system: |
      You are a friendly, helpful assistant. You can read files
      and summarize content. Use your tools when the user asks
      for something that requires them.
  skills:
    - read-file
    - summarize
provenance:
  authored_by: human
  version: 1.0.0
```

### 5. Add skills directly in a topology

You can also bind skills at the topology level (overrides or extends the archetype):

```yaml
# topologies/hello.yaml — with skill override
agents:
  root:
    id: assistant
    role: root
    archetype: friendly-assistant
    skills_additional:
      - quality-check    # adds to archetype skills
```

`skills_additional` adds to the archetype's skills. `skills` replaces them entirely.

### 6. Skill with retry and failure handling

```yaml
# skills/fetch-data.yaml
apiVersion: swarmkit/v1
kind: Skill
metadata:
  id: fetch-data
  name: Fetch Data
  description: Fetch data from an external API.
category: capability
implementation:
  type: mcp_tool
  server: api-client
  tool: fetch
constraints:
  timeout_seconds: 30
  retry:
    attempts: 3
    backoff: exponential
  on_failure: fallback
audit:
  log_inputs: summary
  log_outputs: full
  redact: ["$.api_key", "$.password"]
provenance:
  authored_by: human
  version: 1.0.0
```

This skill retries 3 times with exponential backoff, times out after 30 seconds, and redacts sensitive fields from audit logs.

## Skill categories explained

| Category | Purpose | Example |
|---|---|---|
| `capability` | Do something (read, write, compute) | Read file, call API, search database |
| `decision` | Evaluate and return verdict | Quality check, security scan, code review |
| `coordination` | Coordinate between agents | Peer handoff, task routing |
| `persistence` | Record state for later | Audit log write, checkpoint save |

## Validate

```bash
swarmkit validate . --tree
```

You should see skills listed under each agent:

```
Agent tree:
  assistant (root)
    archetype: friendly-assistant
    model: openrouter/meta-llama/llama-3.3-70b-instruct
    skills: read-file, summarize, quality-check
```

## Your workspace so far

```
my-swarm/
├── workspace.yaml
├── archetypes/
│   ├── friendly-assistant.yaml
│   └── code-explainer.yaml
├── skills/
│   ├── read-file.yaml
│   ├── quality-check.yaml
│   ├── summarize.yaml
│   └── fetch-data.yaml
└── topologies/
    ├── hello.yaml
    └── explain.yaml
```

## Next

[Level 4: Multi-Agent](04-multi-agent.md) — build a team of agents that delegate to each other.
