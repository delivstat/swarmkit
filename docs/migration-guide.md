# SwarmKit — Machine Migration & Local LLM Setup Guide

Complete setup instructions for migrating the SwarmKit development environment to a new machine with local LLM inference via Ollama.

## Prerequisites

### Hardware

- **Minimum:** Any machine with internet access (for Ollama cloud models)
- **For local inference:** 24-48GB+ VRAM (for Qwen 3 32B/72B)
- **Disk:** 20GB free for SwarmKit + dependencies

### Software needed

| Tool | Purpose |
|---|---|
| Python 3.11+ | SwarmKit runtime |
| uv | Python package manager |
| Node.js 22+ | MCP servers, Claude Code, pnpm |
| pnpm | TypeScript package manager |
| Ollama | Local/cloud LLM inference |
| Git | Source control |
| Claude Code | AI-assisted development |

---

## Step 1: System dependencies

```bash
# Ubuntu/Debian
sudo apt update && sudo apt install -y \
  git curl wget build-essential \
  python3.11 python3.11-venv python3.11-dev

# macOS (with Homebrew)
brew install git python@3.11
```

## Step 2: Install uv (Python package manager)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc  # or source ~/.zshrc
uv --version
```

## Step 3: Install Node.js + pnpm

```bash
# Install nvm
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash
source ~/.bashrc

# Install Node.js 22
nvm install 22
node --version  # should show v22.x

# Install pnpm
npm install -g pnpm
pnpm --version
```

## Step 4: Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh

# Verify
ollama --version

# Start the Ollama server (runs in background)
ollama serve &
```

## Step 5: Pull your LLM model

### Option A: Kimi K2.6 cloud (recommended — no VRAM, no API keys)

```bash
ollama pull kimi-k2.6:cloud
```

- Routes to Moonshot's servers via Ollama
- No GPU/VRAM needed
- No API key or signup required
- 96.6% tool calling success rate
- 256K context window

### Option B: Qwen 3 32B local (needs ~24GB VRAM)

```bash
ollama pull qwen3:32b
```

- Runs fully local — no internet needed after download
- Strong tool calling, good JSON schema adherence
- Apache 2.0 license

### Option C: Qwen 3 72B local (needs ~48GB VRAM)

```bash
ollama pull qwen3:72b
```

- Best open-source model for structured tool calling
- Fits with Q4 quantization on 48GB+ VRAM
- Apache 2.0 license

### Verify model works

```bash
ollama run kimi-k2.6:cloud "Say hello"
# or
ollama run qwen3:32b "Say hello"
```

## Step 6: Clone SwarmKit

```bash
cd ~/development  # or your preferred directory
git clone git@github.com:delivstat/swarmkit.git
cd swarmkit

# Checkout the current working branch
git checkout fix/authoring-validate-before-write
```

## Step 7: Install SwarmKit dependencies

```bash
# Python packages (runtime + schema + dev tools)
uv sync --all-packages

# TypeScript packages (optional — only for UI/schema TS work)
pnpm install
```

## Step 8: Verify the installation

```bash
# 1. Validate the hello-swarm example
uv run swarmkit validate examples/hello-swarm/workspace --tree

# Expected output:
# ✓ workspace: hello-swarm
#   topologies: 1   (hello)
#   skills:     1   (say-hello)
#   archetypes: 1   (greeter)
#   triggers:   0   (—)
#
# no errors, 0 warnings

# 2. Run the test suite
uv run pytest packages/runtime/tests -q

# Expected: 199 passed

# 3. Run the hello-swarm with your model
SWARMKIT_PROVIDER=ollama SWARMKIT_MODEL=kimi-k2.6:cloud \
  uv run swarmkit run examples/hello-swarm/workspace hello \
  --input "Greet the engineering team"

# Expected: a greeting from the model

# 4. Test the authoring flow
SWARMKIT_PROVIDER=ollama SWARMKIT_MODEL=kimi-k2.6:cloud \
  uv run swarmkit init ./test-workspace

# Expected: interactive conversation asking what the swarm should do
# Type 'exit' to end the session
```

## Step 9: Migrate Claude Code context

Claude Code stores project memory (architectural decisions, user
preferences, coding style) in files on disk. Copy them to preserve
context across machines.

### On the old machine

```bash
# Package the memory files
tar czf ~/swarmkit-memory.tar.gz \
  ~/.claude/projects/-root-development-swarmkit/

# Transfer to new machine (pick one)
scp ~/swarmkit-memory.tar.gz new-machine:~/
# or
rsync -avz ~/swarmkit-memory.tar.gz new-machine:~/
# or copy via USB drive / cloud storage
```

### On the new machine

```bash
# Create the Claude projects directory
mkdir -p ~/.claude/projects/

# Extract the memory
# Note: adjust --strip-components based on your source path depth
tar xzf ~/swarmkit-memory.tar.gz -C ~/.claude/projects/ \
  --strip-components=3

# Verify
ls ~/.claude/projects/-root-development-swarmkit/memory/
# Should show: MEMORY.md and various .md memory files
```

### What the memory contains

| File | What it remembers |
|---|---|
| `MEMORY.md` | Index of all memories |
| `user_role.md` | Your role, preferences, how you work |
| `feedback_*.md` | Your corrections and confirmed approaches |
| `project_*.md` | Architectural decisions, design patterns |

## Step 10: Install Claude Code

```bash
npm install -g @anthropic-ai/claude-code

# Start Claude Code in the SwarmKit directory
cd ~/development/swarmkit
claude
```

Claude Code will automatically load:
- `CLAUDE.md` files from the repo (coding standards, invariants)
- Memory files from `~/.claude/projects/` (your preferences, context)

---

## Quick reference: running SwarmKit with your model

```bash
# Authoring — create a workspace
SWARMKIT_PROVIDER=ollama SWARMKIT_MODEL=kimi-k2.6:cloud \
  uv run swarmkit init ./my-workspace

# Authoring — add a skill to existing workspace
SWARMKIT_PROVIDER=ollama SWARMKIT_MODEL=kimi-k2.6:cloud \
  uv run swarmkit author skill ./my-workspace

# Authoring — add an MCP server
SWARMKIT_PROVIDER=ollama SWARMKIT_MODEL=kimi-k2.6:cloud \
  uv run swarmkit author mcp-server ./my-workspace

# Validate a workspace
uv run swarmkit validate ./my-workspace --tree

# Run a topology
SWARMKIT_PROVIDER=ollama SWARMKIT_MODEL=kimi-k2.6:cloud \
  uv run swarmkit run ./my-workspace <topology-name> \
  --input "your task here"

# Bundle knowledge pack
uv run swarmkit knowledge-pack ./my-workspace -o pack.md

# List review items
uv run swarmkit review list ./my-workspace

# List skill gaps
uv run swarmkit gaps ./my-workspace
```

## Environment variables reference

```bash
# Model selection (set in ~/.bashrc or ~/.zshrc for persistence)
export SWARMKIT_PROVIDER=ollama
export SWARMKIT_MODEL=kimi-k2.6:cloud

# Optional: override for authoring specifically
export SWARMKIT_AUTHOR_MODEL=ollama/kimi-k2.6:cloud

# Optional: cloud provider API keys (not needed for Ollama)
# export GOOGLE_API_KEY="..."
# export ANTHROPIC_API_KEY="..."
# export GROQ_API_KEY="..."
# export OPENAI_API_KEY="..."
```

## Troubleshooting

### Ollama server not running

```bash
# Check if Ollama is running
curl http://localhost:11434/api/tags
# If connection refused:
ollama serve &
```

### Model not found

```bash
# List installed models
ollama list
# Pull the model again
ollama pull kimi-k2.6:cloud
```

### Python version issues

```bash
# Check Python version
python3 --version
# SwarmKit requires 3.11+
# If using pyenv:
pyenv install 3.11
pyenv local 3.11
```

### Tests failing after migration

```bash
# Reinstall everything cleanly
uv sync --all-packages --reinstall
uv run pytest packages/runtime/tests -q
```

### Claude Code doesn't have context

```bash
# Verify memory files are in the right place
ls ~/.claude/projects/-root-development-swarmkit/memory/MEMORY.md
# If missing, re-extract from the tar backup
```
