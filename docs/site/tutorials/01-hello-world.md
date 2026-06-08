# Level 1: Hello World

Build your first SwarmKit workspace — one agent that greets users.

## What you'll learn

- Installing SwarmKit
- Creating a workspace manually (YAML)
- Running a topology with `swarmkit run`
- Validating with `swarmkit validate`

## Install

```bash
# Install uv (Python package manager) if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install SwarmKit
uv tool install swarmkit-runtime

# Verify
swarmkit --help
```

<details>
<summary>New to the terminal?</summary>

Open your terminal (Terminal on Mac, Command Prompt or WSL on Windows). Copy each command and press Enter. The `$` symbol means "type this in the terminal" — don't type the `$` itself.

</details>

## Build it

Create a project directory:

```bash
mkdir my-swarm && cd my-swarm
```

### 1. Workspace file

Every SwarmKit project starts with `workspace.yaml` — it defines your workspace:

```yaml
# workspace.yaml
apiVersion: swarmkit/v1
kind: Workspace
metadata:
  id: my-swarm
  name: My First Swarm
  description: Learning SwarmKit step by step.
governance:
  provider: mock
```

`governance.provider: mock` means no real policy enforcement — perfect for learning.

### 2. Topology file

A topology defines which agents exist and how they connect. Create `topologies/hello.yaml`:

```bash
mkdir topologies
```

```yaml
# topologies/hello.yaml
apiVersion: swarmkit/v1
kind: Topology
metadata:
  id: hello
  name: Hello World
  description: A single agent that greets users.
agents:
  root:
    id: greeter
    role: root
    model:
      provider: mock
      name: mock
    prompt:
      system: |
        You are a friendly greeter. When someone sends you a message,
        respond with a warm, personalized greeting. Keep it short —
        2-3 sentences max.
```

That's it — one agent (`greeter`) with a system prompt. The `role: root` means it's the entry point.

### 3. Validate

Check that everything is correct:

```bash
swarmkit validate . --tree
```

You should see:

```
✓ Workspace 'my-swarm' is valid.

Agent tree:
  greeter (root)
    model: mock/mock
    skills: (none)
```

### 4. Run it

Execute the topology:

```bash
swarmkit run . hello --input "Hi! My name is Alex."
```

With the mock provider, you'll get a placeholder response. To get a real response, set up a model provider:

```bash
# Option 1: OpenRouter (recommended — access to 100+ models)
export OPENROUTER_API_KEY=your-key-here

# Option 2: Ollama (free, local, no API key)
# Install Ollama from https://ollama.ai, then:
# ollama pull llama3.2
```

Update your topology to use a real provider:

```yaml
# topologies/hello.yaml — updated model section
    model:
      provider: openrouter
      name: meta-llama/llama-3.3-70b-instruct
      temperature: 0.7
```

Or for Ollama:

```yaml
    model:
      provider: ollama
      name: llama3.2
```

Run again:

```bash
swarmkit run . hello --input "Hi! My name is Alex."
```

Now you'll get a real greeting from the LLM.

### 5. Try more options

```bash
# See what the agent tree looks like without running
swarmkit run . hello --input "test" --dry-run

# Verbose mode — see model calls and timing
swarmkit run . hello --input "Hello!" --verbose

# JSON output for piping to other tools
swarmkit validate . --json
```

## What happened

1. `workspace.yaml` told SwarmKit this is a valid workspace
2. `topologies/hello.yaml` defined one agent with a system prompt
3. `swarmkit validate` loaded and resolved everything
4. `swarmkit run` compiled the topology to a LangGraph graph, sent your input to the model, and returned the output

## Your workspace so far

```
my-swarm/
├── workspace.yaml
└── topologies/
    └── hello.yaml
```

## Next

[Level 2: Archetypes](02-archetypes.md) — make your agent config reusable.
