# Level 13: Authoring & Review

Create topologies, skills, and archetypes through conversation — let the AI write the YAML for you.

## What you'll learn

- `swarmkit init` — create a workspace from conversation
- `swarmkit author` — create topologies, skills, archetypes, MCP servers
- `swarmkit edit` — modify a workspace through conversation
- Thorough mode (multi-agent authoring swarm)
- Review queues and skill gap detection

## Conversational authoring

You've been writing YAML by hand in Levels 1-12. In practice, you can let SwarmKit write it for you.

### 1. Create a workspace from scratch

```bash
swarmkit init my-new-swarm/
```

SwarmKit asks you questions about what you're building:
- What does the swarm do?
- How many agents?
- What tools does it need?
- Which model provider?

Then generates the full workspace: `workspace.yaml`, topologies, archetypes, skills.

### 2. Author individual artifacts

```bash
# Create a new topology through conversation
swarmkit author topology my-swarm/

# Create a new skill
swarmkit author skill my-swarm/

# Create a new archetype
swarmkit author archetype my-swarm/

# Create a new MCP server
swarmkit author mcp-server my-swarm/
```

Each command starts a conversation where you describe what you need. The authoring agent:
1. Asks clarifying questions
2. Searches existing workspace artifacts for context
3. Generates valid YAML
4. Validates against JSON Schemas
5. Saves to the workspace

### 3. Thorough mode

For complex artifacts, use `--thorough` to activate the multi-agent authoring swarm (6 specialist agents):

```bash
swarmkit author topology my-swarm/ --thorough
```

The authoring swarm includes:
- **Conversation leader** — talks to you
- **Knowledge searcher** — checks existing capabilities
- **Schema drafter** — generates YAML
- **Artifact validator** — validates against schemas
- **Test writer** — creates tests
- **Artifact publisher** — saves to workspace

### 4. Edit existing workspace

```bash
swarmkit edit my-swarm/ --input "Add a security reviewer to the content-team topology"
```

The edit command:
1. Loads the existing workspace
2. Understands your modification request
3. Modifies the relevant files
4. Validates the changes
5. Saves

You can also use it interactively (no `--input`):

```bash
swarmkit edit my-swarm/
```

## Review queues

When governance or decision skills flag something for human review, it enters a queue.

### 5. List pending reviews

```bash
swarmkit review list my-swarm/
```

### 6. Review an item

```bash
# See full details
swarmkit review show abc123 my-swarm/

# Approve
swarmkit review approve abc123 my-swarm/

# Reject
swarmkit review reject abc123 my-swarm/
```

Reviews are triggered when:
- A decision skill returns `verdict: needs-review`
- A governance evaluation has low confidence
- An agent requests elevated permissions

## Skill gap detection

SwarmKit tracks when agents try to use skills that don't exist:

```bash
swarmkit gaps my-swarm/
```

Output:
```
Skill gaps detected:
  - "translate-text" requested 12 times by agent "writer"
    → Consider creating a translation skill
  - "search-database" requested 5 times by agent "researcher"
    → Consider adding a database MCP server
```

Configure the threshold in your topology:

```yaml
runtime:
  skill_gap_logging:
    enabled: true
    surface_threshold: 3    # surface after 3 occurrences
```

## Your workspace so far

At this point, you can create and modify everything through conversation. The workspace structure is complete:

```
my-swarm/
├── workspace.yaml
├── archetypes/
├── skills/
├── topologies/
├── servers/
├── gates/
├── triggers/
├── knowledge/
├── scripts/
└── .swarmkit/
    ├── conversations/
    ├── memory.json
    ├── audit.sqlite
    └── prompts.sqlite
```

## Next

[Level 14: Packaging & Distribution](14-packaging.md) — share your workspace with others.
