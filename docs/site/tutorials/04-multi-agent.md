# Level 4: Multi-Agent Topologies

Build a team of agents that delegate tasks to each other — root coordinators, leader managers, and worker specialists.

## What you'll learn

- Agent hierarchy (root → leader → worker)
- Delegation between agents
- Parallel execution
- DAG dependencies (`depends_on`)
- Per-agent model and prompt overrides

## How delegation works

In SwarmKit, agents don't call each other directly. The root agent receives the user's input and decides which child agent should handle it. The child does the work and returns a result to the parent. The parent synthesizes and responds.

```
User input → Root (coordinator)
               ├── Leader 1 (research)
               │   ├── Worker A (search)
               │   └── Worker B (analyze)
               └── Leader 2 (writing)
                   └── Worker C (draft)
```

## Build it

### 1. Create specialist archetypes

```yaml
# archetypes/researcher.yaml
apiVersion: swarmkit/v1
kind: Archetype
metadata:
  id: researcher
  name: Researcher
  description: Investigates topics and gathers information.
role: worker
defaults:
  model:
    provider: openrouter
    name: meta-llama/llama-3.3-70b-instruct
    temperature: 0.3
  prompt:
    system: |
      You are a thorough researcher. When given a topic, provide
      well-organized findings with sources where possible. Focus
      on facts, not opinions.
  skills:
    - summarize
provenance:
  authored_by: human
  version: 1.0.0
```

```yaml
# archetypes/writer.yaml
apiVersion: swarmkit/v1
kind: Archetype
metadata:
  id: writer
  name: Writer
  description: Writes clear, engaging content based on research.
role: worker
defaults:
  model:
    provider: openrouter
    name: deepseek/deepseek-chat-v3-0324
    temperature: 0.7
  prompt:
    system: |
      You are a skilled writer. Take research findings and turn
      them into clear, engaging content. Match the requested
      format (blog post, report, email, etc.).
provenance:
  authored_by: human
  version: 1.0.0
```

```yaml
# archetypes/coordinator.yaml
apiVersion: swarmkit/v1
kind: Archetype
metadata:
  id: coordinator
  name: Coordinator
  description: >
    Routes tasks to the right specialist. Doesn't do the work
    itself — delegates and synthesizes results.
role: root
defaults:
  model:
    provider: openrouter
    name: meta-llama/llama-3.3-70b-instruct
    temperature: 0.3
  prompt:
    system: |
      You are a coordinator. Your job is to understand the user's
      request, delegate to the right specialist, and synthesize
      their output into a final response. You have two specialists:
      - researcher: for investigation and fact-finding
      - writer: for drafting content
      Delegate to one or both depending on the task.
provenance:
  authored_by: human
  version: 1.0.0
```

### 2. Create a multi-agent topology

```yaml
# topologies/content-team.yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  id: content-team
  name: Content Team
  description: >
    A coordinator delegates research and writing tasks to
    specialist agents.
agents:
  root:
    id: coordinator
    role: root
    archetype: coordinator
    children:
      - id: researcher
        role: worker
        archetype: researcher
      - id: writer
        role: worker
        archetype: writer
```

Three agents: `coordinator` (root) delegates to `researcher` and `writer`.

### 3. Validate and run

```bash
# See the agent tree
swarmkit validate . --tree

# Output:
#   coordinator (root)
#     archetype: coordinator
#     model: openrouter/meta-llama/llama-3.3-70b-instruct
#     ├── researcher (worker)
#     │   archetype: researcher
#     │   skills: summarize
#     └── writer (worker)
#         archetype: writer

# Run it
swarmkit run . content-team \
  --input "Write a short blog post about the benefits of meditation"
```

The coordinator will:
1. Delegate research to the `researcher` agent
2. Delegate writing to the `writer` agent (using research results)
3. Synthesize the final output

### 4. Parallel execution

When children are independent, they run in parallel:

```yaml
# topologies/parallel-research.yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  id: parallel-research
  name: Parallel Research
  description: Three researchers work simultaneously.
agents:
  root:
    id: coordinator
    role: root
    archetype: coordinator
    children:
      - id: researcher-tech
        role: worker
        archetype: researcher
        prompt:
          system: You research technology trends only.
      - id: researcher-health
        role: worker
        archetype: researcher
        prompt:
          system: You research health and wellness only.
      - id: researcher-finance
        role: worker
        archetype: researcher
        prompt:
          system: You research financial markets only.
```

The coordinator can delegate to all three simultaneously — they run in parallel.

### 5. DAG dependencies

When one agent's output feeds another, use `depends_on`:

```yaml
# topologies/pipeline.yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  id: pipeline
  name: Research-then-Write Pipeline
  description: Research first, then write using the research.
agents:
  root:
    id: coordinator
    role: root
    archetype: coordinator
    children:
      - id: researcher
        role: worker
        archetype: researcher
      - id: writer
        role: worker
        archetype: writer
        depends_on: [researcher]
```

`depends_on: [researcher]` means the writer waits for the researcher to finish before starting. The coordinator handles the sequencing automatically.

### 6. Three-tier hierarchy

Add a middle management layer:

```yaml
# topologies/review-team.yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  id: review-team
  name: Review Team
  description: Leaders manage workers, root coordinates leaders.
agents:
  root:
    id: manager
    role: root
    archetype: coordinator
    children:
      - id: research-lead
        role: leader
        archetype: coordinator
        prompt:
          system: You lead the research team. Delegate to your workers.
        children:
          - id: searcher
            role: worker
            archetype: researcher
            prompt:
              system: You search for information on the given topic.
          - id: fact-checker
            role: worker
            archetype: researcher
            prompt:
              system: You verify facts and check sources.
      - id: writing-lead
        role: leader
        archetype: coordinator
        prompt:
          system: You lead the writing team. Delegate to your workers.
        children:
          - id: drafter
            role: worker
            archetype: writer
          - id: editor
            role: worker
            archetype: writer
            prompt:
              system: You edit and polish drafts for clarity and style.
```

Six agents in three tiers — the root delegates to leaders, leaders delegate to workers.

## Run with verbose output

```bash
swarmkit run . review-team \
  --input "Write a fact-checked article about AI safety" \
  --verbose
```

Verbose mode shows each agent's execution: which tools they called, how long they took, and what they returned.

## Your workspace so far

```
my-swarm/
├── workspace.yaml
├── archetypes/
│   ├── friendly-assistant.yaml
│   ├── code-explainer.yaml
│   ├── researcher.yaml
│   ├── writer.yaml
│   └── coordinator.yaml
├── skills/
│   ├── read-file.yaml
│   ├── quality-check.yaml
│   ├── summarize.yaml
│   └── fetch-data.yaml
└── topologies/
    ├── hello.yaml
    ├── explain.yaml
    ├── content-team.yaml
    ├── parallel-research.yaml
    ├── pipeline.yaml
    └── review-team.yaml
```

## Next

[Level 5: MCP Tools](05-mcp-tools.md) — give your agents real tools that interact with the world.
