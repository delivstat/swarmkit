---
title: Skill catalogue — the first hundred
description: Which MCP servers the public catalogue should carry, triangulated across six sources, ordered by what the liveness check can actually verify rather than by popularity alone.
tags: [skills, ecosystem, curation]
status: draft
---

# The first hundred

Companion to [`skill-catalogue.md`](skill-catalogue.md), which decided *how* the catalogue works.
This decides *what goes in it*, and in what order.

## "Most used" is not published — this is triangulated

No one publishes MCP server usage. Every ranking online is a proxy, and the proxies disagree
because they measure different things. Six sources, and what each actually measures:

| source | measures | caveat |
| --- | --- | --- |
| [PulseMCP](https://www.pulsemcp.com/servers?sort=popular-total-desc) — 22,000+ servers, aggregates the official registry, Glama, Smithery, mcp.so | **visitors to the server's listing** | interest, not installs |
| [mcpmanager.ai top 50](https://mcpmanager.ai/blog/most-popular-mcp-servers/) | **search volume** (622k/mo worldwide) | demand, and inflated for servers whose names are ordinary words |
| [best-of-mcp-servers](https://github.com/tolkonepiu/best-of-mcp-servers) | **GitHub stars** | often the *parent project's* stars — `microsoft/markitdown` at 180k is a document converter that also ships an MCP server |
| [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | **official reference set** | authoritative, and explicitly "educational examples", not production |
| [mcpservers.org remote list](https://mcpservers.org/remote-mcp-servers) | **vendor-hosted endpoints** | the best signal for *maintained*, which matters more than popular |
| [Docker MCP catalog](https://docs.docker.com/ai/mcp-catalog-and-toolkit/catalog/) — 300+ verified | **passed a review**, signed, SBOM | quality, not usage |

Where they agree, the signal is real: **Playwright, GitHub, Filesystem, Fetch, Context7, Notion,
Supabase, Linear, Slack, Figma and Sequential Thinking appear near the top of every list.** Where
they disagree, this document says so rather than averaging them into false precision.

One pattern worth naming: the top of every ranking is browser automation, documentation lookup and
code tools. That is the coding-agent workload, because coding agents are who adopted MCP first — not
evidence that nobody wants a CRM.

## The finding that shapes the catalogue

Sampling 76 servers that recur across these sources, and asking the only question our liveness check
cares about — *can we start this without somebody's account?*

```
 21  28%  verifiable as-is                   starts with no secret
 10  13%  verifiable in CI with a container  we can stand the dependency up ourselves
 45  59%  unverifiable in public CI          needs someone's account
```

**Roughly three in five of the most-wanted servers can never carry a verified badge in public CI.**
That is not a problem to design around; it is the thing to be honest about, and it is why
`unverifiable` is a first-class state rather than a silent pass.

It also produces one concrete recommendation. The 13% — Postgres, Redis, MongoDB, Elasticsearch,
Neo4j, Qdrant, ClickHouse, Kubernetes — are unverifiable only because nothing stands the dependency
up. GitHub Actions `services:` can. **Containerising those moves a seventh of the catalogue from
"we did not look" to "verified on a date"**, and they are exactly the servers where a silent schema
change is most likely to hurt.

## Tier A — verify as-is (seed the catalogue with these)

No credential, no container. The liveness check works on day one, so the badge means something
before anything else is added.

| # | server | maintainer | why it is here |
| --- | --- | --- | --- |
| 1 | **Playwright** | Microsoft | #1 or #2 in every source; 76.9m PulseMCP visitors, 82k monthly searches |
| 2 | **Chrome DevTools** | Google | 51.2m visitors — second only to Playwright, and absent from the search-volume list, which is why one source is not enough |
| 3 | **Fetch** | Anthropic | official reference; 32.2m visitors |
| 4 | **Filesystem** | Anthropic | official reference; 16m visitors |
| 5 | **Git** | Anthropic | official reference; 9.1m visitors |
| 6 | **Sequential Thinking** | MCP steering group | official; most-installed on Smithery (5,550+) |
| 7 | **Memory / Knowledge Graph** | Anthropic | official reference; 4.1m visitors |
| 8 | **Time** | Anthropic | official reference; 6.1m visitors |
| 9 | **Context7** | Upstash | 33.5m visitors; live library docs — the highest-value non-Anthropic free entry |
| 10 | **markitdown** | Microsoft | document → markdown; already used in this repo |
| 11 | **DuckDB** | ktanaka101 | 4.9m visitors; embedded, so genuinely no dependency |
| 12 | **SQLite** | community | same argument as DuckDB |
| 13 | **Puppeteer** | community | 7.3k searches; the alternative when Playwright is too heavy |
| 14 | **Browser Use** | Browser Use | 31.1m visitors |
| 15 | **Serena** | oraios | 19k searches, 29k stars; symbolic code operations |
| 16 | **Everything / Demo** | Anthropic | the reference test server — the natural fixture for our own check |
| 17 | **Office Word** | gongrzhe | 5.7m visitors |
| 18 | **Office PowerPoint** | gongrzhe | 1.9m visitors |
| 19 | **XcodeBuild** | Cameron Cooke | 2.4m visitors |
| 20 | **Storybook** | Storybook | 29.8m visitors |
| 21 | **Blender** | Siddharth Ahuja | 2.8m visitors — high interest, narrow audience |

## Tier B — verifiable with a container in CI

Unverifiable only because nothing starts the dependency. `services:` in the workflow fixes that, and
these are where an unnoticed schema change does the most damage.

| # | server | note |
| --- | --- | --- |
| 22 | **PostgreSQL** | 7.9k searches, 3.7m visitors; the most-wanted database |
| 23 | **MySQL** | 4.2k searches |
| 24 | **Redis** | 1.4k searches |
| 25 | **MongoDB** | official (MongoDB Inc.), 3.2m visitors |
| 26 | **Elasticsearch** | Docker-verified partner |
| 27 | **ClickHouse** | analytics workloads |
| 28 | **Neo4j** | graph queries |
| 29 | **Qdrant** | vector search |
| 30 | **Chroma** | vector search, embedded option |
| 31 | **Kubernetes** | 2.1k searches |
| 32 | **Docker** | 10.3k searches |

## Tier C — vendor-hosted and vendor-maintained (`unverifiable`, low rot)

Credentialed, so permanently `unverifiable` in public CI — but the vendor maintains them, which is
the risk that actually matters. A vendor-run endpoint is far less likely to silently rename a tool
than a community wrapper around the same API.

**Development:** GitHub · GitLab · Linear · Atlassian (Jira/Confluence) · Sentry · Vercel ·
Railway · Neon · Supabase · Cloudflare · AWS · Azure · Docker Hub · CircleCI · Netlify

**Product and design:** Figma · Canva · Notion · Storybook · PostHog · Amplitude

**Communication and work:** Slack · Asana · Monday · ClickUp · Trello · Box · Google Drive · Gmail ·
Google Calendar · Google Sheets · Microsoft 365 · Outlook

**Commerce and finance:** Stripe · Shopify · Mercury · Plaid · QuickBooks · Xero · PayPal

**CRM and marketing:** Salesforce · HubSpot · Attio · Apollo.io · Intercom · Zendesk · Mailchimp

**Data and observability:** Snowflake · Databricks · dbt · Datadog · Grafana · New Relic · Elastic
Cloud · BigQuery

**Automation:** Zapier · n8n · Activepieces · Make

**Search and retrieval:** Brave Search · Tavily · Exa · Firecrawl · Perplexity · Kagi · SerpAPI ·
Parallel Search · Ahrefs

That is roughly the remaining 70, and the count is deliberately approximate: the exact tail matters
less than the ordering, and the tail should be driven by what people ask for rather than by what a
directory happened to list.

## What to actually build first

**Do not seed with a hundred.** Fifteen that provably work beats a hundred that might, and the whole
argument for building verification before the repo was that the badge should mean something on day
one.

Seed with **Tier A 1–16** — the sixteen that need no credential and no container. That is:

- every official Anthropic reference server (7)
- the two browser servers at the top of every ranking
- Context7, the highest-value free third-party entry
- markitdown, DuckDB, SQLite, Puppeteer, Serena, Browser Use

All sixteen verify on day one. The catalogue launches green, honestly.

**Then add Tier B**, once the workflow grows `services:` — and that PR is worth doing for its own
sake, because it converts a seventh of the catalogue from a shrug into a fact.

**Then Tier C, driven by requests.** The ordering within it is a guess; the first issues will be
better evidence than any directory.

## Open questions this raises

1. **Remote/hosted servers do not fit the current shape.** Tier C is mostly OAuth endpoints
   (`https://mcp.linear.app/mcp`), not stdio commands. `mcp_server` supports `transport: http`, but
   the credential story for a catalogue entry — how a user supplies their own token at import —
   is unresolved.
2. **A bundle's floor and state.** Per the "bundle wraps skills" decision: effective floor is the
   **max** of its members', verification state the **worst**. Neither is implemented, because
   bundles do not exist yet.
3. **What is a skill here, versus what is a tool?** GitHub's server exposes 51 tools. A bundle with
   51 skills is not useful to an agent; picking 6 is a curation judgement this document does not
   make.
4. **Licence and attribution.** Carrying a third party's config is not carrying their code, but the
   catalogue should record the upstream licence per entry anyway.
