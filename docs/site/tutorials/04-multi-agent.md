# Level 4: Multi-Agent Topologies

Build a team of agents that delegate tasks to each other — root coordinators, leader managers, and worker specialists.

## What you'll learn

- Agent hierarchy (root → leader → worker) and archetypes per role
- How delegation actually happens: `delegate_to_<child>` tools, or a task plan
- DAG dependencies (`depends_on`) — sequencing that the runtime enforces
- Per-agent prompt overrides
- Reading a multi-agent run: `--verbose`, `swarmkit trace`, the portal's canvas

The finished workspace is `examples/tutorials/04-multi-agent/`. The transcripts below are from real
runs on OpenRouter (`moonshotai/kimi-k2.5` coordinating, `deepseek/deepseek-chat-v3-0324` writing);
the mock provider never delegates — it answers "mock response" and stops — so this is the first level
where a real key shows something the mock cannot.

## How delegation works

Agents do not call each other directly. The root receives the user's input and decides which child
should handle what. A child does its work and returns a result to the parent; the parent synthesizes.

```
User input → Root (coordinator)
               ├── Leader 1 (research)
               │   ├── Worker A (search)
               │   └── Worker B (analyze)
               └── Leader 2 (writing)
                   └── Worker C (draft)
```

Two mechanisms, chosen by the compiler from the shape of the topology:

- **One child, or children with `depends_on`:** the parent gets a `delegate_to_<child>` tool per
  child and calls them in dependency order.
- **Two or more independent children:** the parent gets **task-plan** tools instead
  (`create-task-plan`, `read-task-result`, `create-scope`, …) and writes a plan whose tasks the
  runtime dispatches to the children — Level 6 goes into this.

Either way, what the model can do is exactly the tool list `--verbose` prints.

## Build it

### 1. Specialist archetypes — one per role

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
    name: moonshotai/kimi-k2.5
    temperature: 0.3
  prompt:
    system: |
      You are a thorough researcher. When given a topic, provide
      well-organized findings with sources where possible. Focus
      on facts, not opinions. Be brief: five bullet points at most.
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
      format (blog post, report, email, etc.). Keep it under 150 words.
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
    name: moonshotai/kimi-k2.5
    temperature: 0.3
  prompt:
    system: |
      You are a coordinator. Understand the user's request, delegate
      to the right specialist, and synthesize their output into a
      final response. Always delegate — never do the work yourself.
provenance:
  authored_by: human
  version: 1.0.0
```

```yaml
# archetypes/lead.yaml
apiVersion: swarmkit/v1
kind: Archetype
metadata:
  id: lead
  name: Team lead
  description: A middle-tier agent that delegates to its workers and reports up.
role: leader
defaults:
  model:
    provider: openrouter
    name: moonshotai/kimi-k2.5
    temperature: 0.3
  prompt:
    system: |
      You lead a small team. Delegate the task to your workers,
      combine what they return, and report the result upward.
provenance:
  authored_by: human
  version: 1.0.0
```

Model choice matters here more than in Level 1: coordinating is tool-calling work. Kimi K2.5 writes
task plans reliably; the Llama 3.3 70B this tutorial used to name returned empty responses to the
plan tools three times and gave up. DeepSeek V3 stays on the writer, where prose is the job.

### 2. A coordinator with two specialists

```yaml
# topologies/content-team.yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: content-team
  version: 0.1.0
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

### 3. Validate and run

```bash
swarmkit validate . --tree
```

```
topology: content-team
  coordinator (role=root, archetype=coordinator)
    model: openrouter/moonshotai/kimi-k2.5
    researcher (role=worker, archetype=researcher)
      model: openrouter/moonshotai/kimi-k2.5
      skills: summarize
    writer (role=worker, archetype=writer)
      model: openrouter/deepseek/deepseek-chat-v3-0324
```

```bash
swarmkit run . content-team --input "Write a short blog post about the benefits of meditation" --verbose
```

```
[coordinator] thinking... (kimi-k2.5)
--- [coordinator] calling moonshotai/kimi-k2.5 ---
  tools: ['create-task-plan', 'update-task-plan', 'read-task-result', 'create-scope', 'update-scope', 'read-scope']
  tool_calls: ['create-task-plan']
[coordinator] created task plan: 1 tasks
[coordinator] executing task batch: write-blog-post
[writer] thinking... (deepseek-chat-v3-0324)
[writer] done (21.7s)
  task 'write-blog-post' completed (5 findings)
[coordinator] thinking... (kimi-k2.5)
  tool_calls: ['read-task-result']
  [coordinator] read task result 'write-blog-post' (3062 chars)
[coordinator] done (33.7s)
The blog post has been successfully written. Here's the completed work:

## The Life-Changing Benefits of Meditation (And Why You Should Start Today)
…

── run summary ──
  coordinator              root       6192ms
  writer                   worker    21660ms
  coordinator              root      33709ms

  skills called: 1
  total events: 8
```

Read it as a story: two independent children, so the coordinator got plan tools; it planned one task
and assigned it to the writer (research was not needed for this request — a plan is the model's
call); the writer ran; the coordinator read the result and answered. The run summary is per node,
in order.

### 4. Parallel execution

Independent children can run at once — the plan's tasks in one batch are dispatched together:

```yaml
# topologies/parallel-research.yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: parallel-research
  version: 0.1.0
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
          system: You research technology trends only. Five bullets at most.
      - id: researcher-health
        role: worker
        archetype: researcher
        prompt:
          system: You research health and wellness only. Five bullets at most.
      - id: researcher-finance
        role: worker
        archetype: researcher
        prompt:
          system: You research financial markets only. Five bullets at most.
```

A per-agent `prompt` replaces the archetype's system prompt for that agent only; the model comes
from the archetype.

### 5. DAG dependencies

When one agent's output must feed another, say so — the runtime enforces the order, not the prompt:

```yaml
# topologies/pipeline.yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: pipeline
  version: 0.1.0
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

```bash
swarmkit run . pipeline --input "A 100-word note on why DAG dependencies matter in agent teams" --verbose
```

```
[coordinator] thinking... (kimi-k2.5)
[researcher] thinking... (kimi-k2.5)
[researcher] done (129.6s)
[writer] thinking... (deepseek-chat-v3-0324)
[writer] done (7.7s)
[coordinator] thinking... (kimi-k2.5)
[coordinator] done (19.1s)
DAG (Directed Acyclic Graph) dependencies streamline agent teamwork by enforcing a clear execution
order, ensuring tasks run efficiently. …

── run summary ──
  researcher               worker   129614ms
  writer                   worker     7745ms
  coordinator              root     142017ms
  coordinator              root      19117ms
  total events: 9
```

With `depends_on`, the coordinator got `delegate_to_researcher` / `delegate_to_writer` rather than
plan tools, and the writer could not start until the researcher had finished — whatever the model
would have preferred.

### 6. Three-tier hierarchy

```yaml
# topologies/review-team.yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: review-team
  version: 0.1.0
  description: Leaders manage workers, the root coordinates leaders.
agents:
  root:
    id: manager
    role: root
    archetype: coordinator
    children:
      - id: research-lead
        role: leader
        archetype: lead
        prompt:
          system: You lead the research team. Delegate to your workers and combine their findings.
        children:
          - id: searcher
            role: worker
            archetype: researcher
            prompt:
              system: You search for information on the given topic. Five bullets at most.
          - id: fact-checker
            role: worker
            archetype: researcher
            prompt:
              system: You verify facts and check sources. Five bullets at most.
      - id: writing-lead
        role: leader
        archetype: lead
        prompt:
          system: You lead the writing team. Delegate drafting, then editing.
        children:
          - id: drafter
            role: worker
            archetype: writer
          - id: editor
            role: worker
            archetype: writer
            prompt:
              system: You edit and polish drafts for clarity and style. Return the edited text only.
```

Seven agents in three tiers. The portal's Composer draws it — **Canvas** view:

![review-team on the canvas](../img/tutorials/04-canvas.png)

## Reading a run

```bash
swarmkit run . review-team --input "A fact-checked note on AI safety" --verbose
swarmkit trace <run-id> .        # the call graph with per-agent tokens and cost
swarmkit logs . --last 1         # every event of the last run
```

Verbose output prints each agent's model, tools, tool calls and duration as it happens; `trace`
reads the same run back afterwards as a tree.

## Your workspace so far

```
my-swarm/
├── workspace.yaml
├── archetypes/
│   ├── friendly-assistant.yaml
│   ├── code-explainer.yaml
│   ├── coordinator.yaml
│   ├── lead.yaml
│   ├── researcher.yaml
│   └── writer.yaml
├── skills/
│   ├── summarize.yaml
│   └── quality-check.yaml
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
