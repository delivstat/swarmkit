# Rynko Content Pipeline

Automated content creation workspace for rynko.dev — researches
trending AI topics, writes blog posts and LinkedIn posts with
natural Rynko product tie-ins, reviews for SEO and voice quality.

## What this gives you

| Topology | Use it for |
|---|---|
| `content-pipeline` | Full pipeline: research → blog + LinkedIn → SEO + editorial review |
| `blog-post` | Write a blog post on a given topic with SEO and editorial review |
| `linkedin-post` | Quick LinkedIn post from a topic or URL |

## Prerequisites

- SwarmKit installed (`pip install swarmkit-runtime` or `uv tool install swarmkit-runtime`)
- Node.js 18+ with `npx` (for brave-search and mcp-local-rag)
- An OpenRouter API key (`OPENROUTER_API_KEY`)
- A Brave Search API key (`BRAVE_API_KEY`) — free tier: 2K queries/month
- Hashnode API token (`HASHNODE_TOKEN`) + publication ID (`HASHNODE_PUBLICATION_ID`)
- Dev.to API key (`DEVTO_API_KEY`)

## Setup

```bash
# Set environment variables
export OPENROUTER_API_KEY=sk-or-...
export BRAVE_API_KEY=BSA...
export HASHNODE_TOKEN=...
export HASHNODE_PUBLICATION_ID=...
export DEVTO_API_KEY=...
export RYNKO_KNOWLEDGE_DIR=~/rynko-knowledge

# Prepare knowledge base
mkdir -p ~/rynko-knowledge
# Copy or symlink Rynko docs, past blog posts, product info
cp workspace/knowledge/*.md ~/rynko-knowledge/

# Ingest knowledge into RAG
cd examples/rynko-content/workspace
STERLING_DOCS_DIR=~/rynko-knowledge python scripts/ingest-docs.py
```

## Usage

### Full content pipeline (research → blog → LinkedIn)

```bash
swarmkit run examples/rynko-content/workspace content-pipeline \
  --input "Create content about AI agent output validation" \
  --verbose
```

### Blog post on a specific topic

```bash
swarmkit run examples/rynko-content/workspace blog-post \
  --input "Write about why deterministic validation beats LLM judges for production AI. Tie to Rynko Flow." \
  --verbose
```

### Quick LinkedIn post

```bash
swarmkit run examples/rynko-content/workspace linkedin-post \
  --input "https://some-news-article-about-mcp-ecosystem.html" \
  --verbose
```

### Interactive mode

```bash
swarmkit chat examples/rynko-content/workspace content-pipeline
> Research what's trending in AI agent governance this week
> Write a blog post about the top topic
> Now create a LinkedIn post from that blog
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ content-pipeline topology                            │
│                                                      │
│ root (coordinator)                                   │
│ ├── trend-researcher → brave-search MCP              │
│ │   Finds trending topics, SERP gaps                 │
│ ├── blog-writer → rynko-knowledge MCP                │
│ │   Writes as Srijith Kartha, warm + technical       │
│ ├── seo-reviewer → brave-search MCP                  │
│ │   Keywords, meta desc, internal links              │
│ ├── content-editor                                   │
│ │   Enforces banned words, voice rules, structure    │
│ └── linkedin-writer → rynko-knowledge MCP            │
│     Hook-Story-Grit structure for engagement         │
└─────────────────────────────────────────────────────┘
```

## Content Rules

All content follows strict writing guidelines:

- **Voice:** Warm, explanatory, first-person as Srijith Kartha
- **Tone:** Respectful of alternatives, humble, specific
- **Banned words:** delve, leverage, seamless, cutting-edge, game-changer, etc.
- **Blog structure:** Warm intro → technical body → CTA (no "In Conclusion")
- **LinkedIn structure:** Hook-Story-Grit (not Hook-Story-Lesson)
- **SEO:** Meta descriptions, canonical URLs, internal links to docs.rynko.dev

## Files

```
workspace/
├── workspace.yaml              # 3 MCP servers (brave-search, blog-publisher, rynko-knowledge)
├── topologies/
│   ├── content-pipeline.yaml   # Full pipeline (5 agents)
│   ├── blog-post.yaml          # Blog-only (4 agents)
│   └── linkedin-post.yaml      # LinkedIn-only (3 agents)
├── archetypes/
│   ├── trend-researcher.yaml   # AI topic research + SERP analysis
│   ├── blog-writer.yaml        # Technical blog posts as Srijith
│   ├── linkedin-writer.yaml    # Hook-Story-Grit LinkedIn posts
│   ├── seo-reviewer.yaml       # SEO quality review
│   └── content-editor.yaml     # Voice and rules compliance
├── skills/
│   ├── search-web.yaml         # Brave web search
│   ├── search-rynko-docs.yaml  # RAG search over Rynko docs
│   ├── publish-hashnode.yaml   # Publish to blog.rynko.dev
│   └── publish-devto.yaml      # Cross-post to dev.to
└── knowledge/
    └── rynko-overview.md       # Product overview for RAG ingestion
```
