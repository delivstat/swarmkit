---
title: Topology Composer UI — three-view visual editor (design §15.2)
description: Visual topology editor with Structure (org-chart), Relationships (per-agent zoom), and Network (node-and-edge graph) views. Hybrid form + YAML editing.
tags: [ui, topology, composer, authoring]
status: design
---

# Topology Composer UI

## Design reference

- §15.1 — three primary UI surfaces (Composer, Authoring, Dashboard)
- §15.2 — three composer views (Structure, Relationships, Network)
- §9.1 — UI reads/writes topology files, decoupled from runtime
- §9.2 — UI syncs to workspace directory on disk
- packages/ui/CLAUDE.md — four invariants (thin layer, schema validation, same authoring swarm, audit read-only)

## Problem

Topologies are YAML files with recursive agent trees, archetype
inheritance, skill assignments, IAM scopes, governance bindings, and
dependency ordering. Creating and modifying them requires understanding
the schema deeply. The CLI authoring agent helps with creation from
scratch, but for visual understanding and surgical modifications, a
graphical editor is faster.

## The three views (per §15.2)

The Composer is **one surface with three views** of the same topology.
A tab bar or segmented control switches between them. All three share
the same Zustand state — switching views doesn't reload data.

### 1. Structure View — "who reports to whom"

**Optimized for:** defining agent hierarchy, levels, membership.

**Visual style:** Org-chart layout. Root at top, leaders in columns
beneath, workers under each leader. Click any node to open the
inspector (property panel).

```
                    ┌──────────┐
                    │   root   │
                    │ supervisor│
                    └─────┬────┘
              ┌───────────┼───────────┐
        ┌─────┴─────┐          ┌─────┴─────┐
        │  eng-lead  │          │  qa-lead   │
        │  leader    │          │  leader    │
        └─────┬─────┘          └─────┬─────┘
        ┌─────┼─────┐          ┌─────┼─────┐
   ┌────┴───┐ ┌────┴───┐ ┌────┴───┐ ┌────┴───┐
   │reviewer│ │ reader │ │tester  │ │coverage│
   │ worker │ │ worker │ │ worker │ │ worker │
   └────────┘ └────────┘ └────────┘ └────────┘
```

**Interactions:**
- Click node → select agent, open property panel on right
- Drag node → rearrange hierarchy (move worker between leaders)
- `[+ Add]` button on each node → add child agent
- Right-click → context menu (add child, duplicate, delete, cut/paste)
- Drag from archetype palette → create new agent with that archetype
- Color coding by role: root (purple), leader (blue), worker (green)
- Badge on each node showing agent count of skills

### 2. Relationships View — "what does this agent do"

**Optimized for:** configuring per-agent skills, IAM, communication
paths, validation gates.

**Visual style:** Zoomed view of a single agent showing:
- The agent in the center
- Direct parent above, direct children below
- Skills connected as small nodes on the left
- Governance gates (decision skills) on the right
- IAM scopes listed below
- Archetype source shown as a dotted connection

```
                  ┌──────────┐
                  │ eng-lead │ (parent)
                  └─────┬────┘
                        │
   ┌─────────┐    ┌─────┴─────┐    ┌──────────────┐
   │code-rev │←───│  reviewer  │───→│ grounding    │
   │security │    │  worker    │    │ verifier     │
   │ (skills)│    │            │    │ (governance) │
   └─────────┘    └────────────┘    └──────────────┘
                        │
                  ┌─────┴─────┐
                  │ archetype:│
                  │ code-     │
                  │ analyst   │
                  └───────────┘
```

**Interactions:**
- Click any agent in Structure View → Relationships View shows that agent
- Drag skills from palette onto the agent
- Click governance gates to configure trigger/scope
- See resolved vs overridden properties (archetype defaults dimmed)

### 3. Network View — "how does the swarm communicate"

**Optimized for:** understanding the topology as a communication
graph. Useful for structural review, spotting isolated agents, and
understanding delegation flow.

**Visual style:** Flat node-and-edge layout (react-flow or similar).
All agents as nodes, edges show delegation paths. DAG dependencies
shown as dashed lines. Skill-sharing shown as shared colors.

```
   [root] ──────→ [eng-lead] ──→ [reviewer]
      │                     ──→ [reader]
      │                     ──→ [security]
      └──────→ [qa-lead]  ──→ [tester]
                           ──→ [coverage]

   ─── delegation    - - - depends_on
```

**Interactions:**
- Zoom/pan the graph
- Click node → select agent
- Drag nodes to rearrange layout (cosmetic, doesn't change hierarchy)
- Edge labels show delegation tool names
- Filter: show only leaders, show only workers, show by skill category

## Property panel (shared across all views)

Slides in from the right when an agent is selected. Same panel
regardless of which view is active.

**Sections:**

| Section | Fields | Notes |
|---------|--------|-------|
| Identity | id, role (dropdown), archetype (dropdown) | id is kebab-case validated |
| Model | provider, name, temperature, max_tokens | Shows "from archetype" when inherited |
| Prompt | system (textarea), persona (textarea) | Expandable, preview with token count |
| Skills | List with category badges, add/remove | Toggle: replace vs extend archetype |
| IAM | base_scope tags, elevated_scopes tags | Tag input with autocomplete |
| Dependencies | depends_on multi-select | Only shows sibling agent IDs |
| Output Schema | JSON editor or null toggle | For structured output opt-in/out |
| Intent Monitoring | enabled, threshold, on_drift | Topology or agent level |
| Governance | Decision skill bindings for this agent | Shows workspace + topology merged |

**Archetype inheritance display:**
- Inherited values shown in muted text with "(from archetype: X)" label
- Override checkbox enables topology-level override
- When overridden, field becomes fully editable and bold
- Clear override button reverts to archetype default

## YAML panel (toggleable)

A collapsible panel at the bottom or right edge showing the raw
topology YAML. Uses CodeMirror (lighter than Monaco, ~200KB vs 2MB).

- Syntax highlighting with YAML mode
- Editable — changes parse and update all views
- Toggle between "source" (topology YAML only) and "resolved" (after archetype merging, read-only)
- Validation errors as inline markers
- Copy button for full YAML
- Keyboard shortcut: Cmd+Shift+Y toggles panel

## Palette sidebar

Available archetypes and skills from the workspace. Shown in the left
panel below the agent tree (Structure View) or as a floating panel
in Relationships/Network views.

**Archetypes:** grouped by role (root, leader, worker). Each shows
name, description, skill count.

**Skills:** grouped by category (capability, decision, coordination,
persistence). Each shows name, implementation type (mcp_tool,
llm_prompt, composed), category badge.

**Search bar** at top filters both archetypes and skills.

**Drag from palette** onto:
- Agent tree node → assigns archetype or skill to that agent
- Empty area in tree → creates new agent with that archetype

## Server CRUD endpoints (new)

### Read (detailed)

| Endpoint | Returns |
|----------|---------|
| `GET /api/topologies/:id` | Topology YAML parsed as JSON + resolved agent tree with merged archetype defaults |
| `GET /api/topologies/:id/yaml` | Raw YAML string |
| `GET /api/archetypes/:id` | Full archetype details (defaults, skills, role, provenance) |
| `GET /api/skills/:id` | Full skill details (category, implementation, I/O schema, constraints) |

### Write

| Endpoint | Body | Action |
|----------|------|--------|
| `PUT /api/topologies/:id` | `{yaml: "..."}` | Validate → write → re-resolve workspace |
| `POST /api/topologies` | `{yaml: "..."}` | Validate → create file → re-resolve |
| `DELETE /api/topologies/:id` | — | Delete file → re-resolve |
| `PUT /api/skills/:id` | `{yaml: "..."}` | Same pattern |
| `PUT /api/archetypes/:id` | `{yaml: "..."}` | Same pattern |

`?dry_run=true` on any write endpoint → validate without persisting.

`POST /api/reload` → re-resolve workspace without writing files
(for when files are changed externally).

### Validation response

```json
{
  "valid": true,
  "errors": [],
  "warnings": [],
  "resolved": {
    "agents": [
      {
        "id": "reviewer",
        "role": "worker",
        "archetype": "code-analyst",
        "model": {"provider": "openrouter", "name": "kimi-k2.6"},
        "skills": ["code-quality-review", "security-scan"],
        "inherited": {
          "model": "archetype:code-analyst",
          "skills": "archetype:code-analyst"
        }
      }
    ]
  }
}
```

## State management

```typescript
interface ComposerState {
  // Current topology
  topologyId: string | null;
  yaml: string;
  agents: AgentNode[];          // recursive tree
  metadata: { name, version, description };
  runtime: RuntimeConfig;
  governance: GovernanceConfig;

  // Workspace context (loaded once)
  archetypes: ArchetypeDetail[];
  skills: SkillDetail[];

  // UI state
  activeView: "structure" | "relationships" | "network";
  selectedAgentId: string | null;
  yamlPanelOpen: boolean;
  validationErrors: ValidationError[];
  isDirty: boolean;
  isSaving: boolean;

  // Actions
  selectAgent(id: string): void;
  updateAgent(id: string, patch: Partial<Agent>): void;
  addChild(parentId: string, agent: Agent): void;
  removeAgent(id: string): void;
  moveAgent(agentId: string, newParentId: string): void;
  assignArchetype(agentId: string, archetypeId: string): void;
  assignSkill(agentId: string, skillId: string): void;
  removeSkill(agentId: string, skillId: string): void;
  setYaml(yaml: string): void;
  save(): Promise<void>;
  validate(): Promise<ValidationResult>;
  switchView(view: string): void;
}
```

## Implementation plan

### PR 1: Server CRUD endpoints
- Read endpoints for topology/skill/archetype details
- Write endpoints with validate → write → re-resolve
- dry_run support, reload endpoint
- Tests

### PR 2: Composer page + Structure View
- `/composer` page with view tabs (Structure/Relationships/Network)
- `/composer/:topologyId` for editing existing
- Structure View: org-chart tree rendering (custom component, not react-flow)
- Agent nodes with role badges, skill counts
- Click to select, property panel slides in
- Archetype/skill palette sidebar

### PR 3: Property panel + editing
- Full property form (identity, model, prompt, skills, IAM, etc.)
- Archetype inheritance display (inherited vs overridden)
- Save → PUT /api/topologies/:id
- Live validation (debounced dry_run on each change)

### PR 4: Relationships View + Network View
- Relationships: centered agent with skills/governance/archetype connections
- Network: react-flow based node-and-edge graph
- Shared selection state across all three views

### PR 5: YAML panel + drag-and-drop
- CodeMirror editor with YAML mode
- Bidirectional sync (YAML ↔ tree/properties)
- Source vs resolved toggle
- Drag-and-drop for rearranging hierarchy and assigning from palette

### PR 6: Create new topology + skill/archetype forms
- New topology wizard (minimal template + guided setup)
- Create skill dialog (category, implementation type, I/O schema)
- Create archetype dialog (role, defaults, skills)

## Open questions

1. **react-flow for Network View** — worth the bundle size (~300KB)?
   Alternative: D3 force layout or CSS grid. react-flow is the most
   React-native graph library and handles zoom/pan/selection well.

2. **Offline editing** — should the composer work without the server
   running? Would need client-side YAML validation via `@swarmkit/schema`.
   Nice for authoring but adds complexity.

3. **Collaborative editing** — future. Would need WebSocket sync
   and conflict resolution. Out of scope for v1.

4. **Undo/redo** — Zustand temporal middleware tracks state history.
   Per-action granularity (each agent edit is one undo step).
