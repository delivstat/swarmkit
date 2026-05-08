# Rynko — Product Overview

Rynko is a developer platform built for AI agents and developers. Three products, one platform.

## Rynko Flow — AI Output Validation Gateway

Agents submit payloads to a "Gate". Flow validates against:
1. JSON Schema (structural validation)
2. Expression-based business rules (field-level logic)
3. Optional AI Judge (LLM semantic evaluation — costs 5x a standard run)

When validation fails or confidence is low, Flow routes to human approval via magic links (no account required for approvers). Every run produces an immutable audit trail.

Flow auto-exposes as MCP tools to LangGraph, CrewAI, AutoGen — agents can call Flow gates as standard MCP tool calls.

**Key numbers:**
- Free tier: 500 runs/month
- Starter: $29/month
- Growth: $99/month
- Scale: $349/month
- AI Judge runs cost 5x standard

**SDKs:** Node.js (@rynko/sdk), Python (rynko), Java
**No-code:** Zapier, Make.com, n8n, Google Sheets add-on

## Rynko Render — Document Generation API

Native rendering engine using Yoga/Flexbox layout (not HTML-to-PDF conversion). This means:
- Sub-second generation (200-500ms typical)
- Consistent output across all environments (no browser dependency)
- 28 component types including charts, barcodes, QR codes, form fields
- Both PDF and Excel output

Visual drag-and-drop template designer for non-developers. Templates are structured JSON — AI agents can design templates programmatically, humans verify and tune via the visual editor.

Version control with resource permission controls. Template promotions from Dev → Staging → Production with team/workspace-level permissions.

**Pricing:** Separate add-on: $19-$119/month packs + one-time credit packs

## Rynko Extract — AI Data Extraction Engine

Schema-driven extraction from unstructured files:
- Supported formats: PDF, images, Excel, CSV, JSON
- Per-field confidence scores (know when to trust the extraction)
- Multi-file conflict detection
- Multi-provider: Anthropic (Claude), OpenAI, Google

The schema defines what you want extracted. The engine returns structured data with confidence scores per field. Integrates with Flow for automated validation of extracted data.

## Klervex — Trade Document Intelligence (built on Rynko)

Klervex (klervex.com) is a vertical product built on Rynko's stack for international trade documents. It demonstrates the Extract → Flow → Render pattern:

1. **Extract** — AI extracts data from uploaded trade documents (invoices, packing lists, BoL)
2. **Flow** — Validates against tariff databases (US HTS, EU TARIC), sanctions lists (OFAC SDN), port codes (UN/LOCODE)
3. **Render** — Generates customs-grade PDFs with cryptographic validation stamps

16 document types. Built in 4 weeks on Rynko's three APIs.

## Technical Stack

- Backend: NestJS (TypeScript), Prisma, PostgreSQL, BullMQ, Redis
- Frontend: Next.js 14, React, Tailwind CSS
- Render engine: PDFKit + Yoga layout (native, not Puppeteer)
- MCP: @modelcontextprotocol/sdk integration (both server and client)
- Monorepo: Turborepo, 9 apps, 25 packages

## Links

- Signup: app.rynko.dev/signup
- Docs: docs.rynko.dev
- Discord: discord.gg/d8cU2MG6
- Blog: blog.rynko.dev
