# Level 2: Archetypes

Extract agent configuration into reusable archetypes — define once, use across topologies.

## What you'll learn

- Creating archetype files
- Model configuration (provider, temperature, max_tokens)
- System prompts and persona
- Referencing archetypes from topologies
- Provenance tracking

## Why archetypes?

In Level 1, the agent's model and prompt were inline in the topology. That works for one agent, but when you have 10 agents across 3 topologies, you don't want to repeat the same config everywhere. Archetypes solve this — define the agent's personality once, reference it by ID.

## Build it

### 1. Create an archetype

```bash
mkdir archetypes
```

```yaml
# archetypes/friendly-assistant.yaml
apiVersion: swarmkit/v1
kind: Archetype
metadata:
  id: friendly-assistant
  name: Friendly Assistant
  description: >
    A warm, helpful assistant that answers questions clearly
    and concisely. Good default for general-purpose agents.
role: worker
defaults:
  model:
    provider: openrouter
    name: meta-llama/llama-3.3-70b-instruct
    temperature: 0.7
    max_tokens: 2048
  prompt:
    system: |
      You are a friendly, helpful assistant. Answer questions
      clearly and concisely. If you don't know something, say so
      honestly. Keep responses under 200 words unless the user
      asks for more detail.
provenance:
  authored_by: human
  version: 1.0.0
```

Key fields:
- `role: worker` — this archetype is for worker agents (not root or leader)
- `defaults.model` — model configuration (provider, name, temperature, max_tokens)
- `defaults.prompt.system` — the system prompt
- `provenance` — who created this and when

### 2. Create a second archetype

```yaml
# archetypes/code-explainer.yaml
apiVersion: swarmkit/v1
kind: Archetype
metadata:
  id: code-explainer
  name: Code Explainer
  description: >
    Explains code clearly with examples. Uses analogies to make
    complex concepts accessible. Always shows before and after.
role: worker
defaults:
  model:
    provider: openrouter
    name: deepseek/deepseek-chat-v3-0324
    temperature: 0.3
    max_tokens: 4096
  prompt:
    system: |
      You are a code explainer. When given code or a programming
      concept, explain it clearly using:
      1. A one-sentence summary
      2. A real-world analogy
      3. A simple code example
      Keep it practical — no theory without examples.
provenance:
  authored_by: human
  version: 1.0.0
```

Notice the different model — `deepseek/deepseek-chat-v3-0324` with lower temperature (0.3) for more precise code explanations.

### 3. Update the topology to use archetypes

```yaml
# topologies/hello.yaml — updated
apiVersion: swarmkit/v1
kind: Topology
metadata:
  id: hello
  name: Hello World
  description: A single agent using an archetype.
agents:
  root:
    id: assistant
    role: root
    archetype: friendly-assistant
```

That's it — `archetype: friendly-assistant` pulls in the model config and prompt from the archetype file.

### 4. Create a second topology

```yaml
# topologies/explain.yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  id: explain
  name: Code Explainer
  description: Explains code concepts clearly.
agents:
  root:
    id: explainer
    role: root
    archetype: code-explainer
```

### 5. Override archetype defaults

You can override any archetype field in the topology:

```yaml
# topologies/explain.yaml — with override
agents:
  root:
    id: explainer
    role: root
    archetype: code-explainer
    model:
      temperature: 0.1    # more deterministic than archetype default
    prompt:
      system: |
        You are a Python specialist. Only explain Python code.
        Use type hints in all examples.
```

The topology override wins — the archetype provides defaults, the topology can customize.

### 6. Validate and run

```bash
# Validate — should show both topologies
swarmkit validate . --tree

# Run the assistant
swarmkit run . hello --input "What's the weather like in Tokyo?"

# Run the code explainer
swarmkit run . explain --input "What is a decorator in Python?"
```

## Model configuration reference

```yaml
defaults:
  model:
    provider: openrouter          # which API to call
    name: meta-llama/llama-3.3   # model identifier
    temperature: 0.7              # 0.0 = deterministic, 1.0 = creative
    max_tokens: 2048              # max output length
    tool_model: gpt-4o-mini       # cheaper model for tool calls (Level 6)
    tool_provider: openai         # provider for tool model
```

## Provenance options

```yaml
provenance:
  authored_by: human              # human | authored_by_swarm | derived_from_template
  version: 1.0.0
  # authored_date: 2026-01-01    # optional
  # registry: npm                 # optional, for published archetypes
  # vendor: delivstat             # optional
```

## Your workspace so far

```
my-swarm/
├── workspace.yaml
├── archetypes/
│   ├── friendly-assistant.yaml
│   └── code-explainer.yaml
└── topologies/
    ├── hello.yaml
    └── explain.yaml
```

## Next

[Level 3: Skills](03-skills.md) — give your agents tools and capabilities.
