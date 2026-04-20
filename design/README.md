# Design

Authoritative architecture documents for SwarmKit.

- **`SwarmKit-Design-v0.6.docx`** — source of truth (Word).
- **`SwarmKit-Design-v0.6.extracted.md`** — plain-text extraction for easy reading by humans and Claude. Regenerate when the docx changes (see `scripts/extract_design.py`).

Detailed design specs (schema, runtime API, GovernanceProvider, per-topology designs) will live alongside v0.6 in this directory once produced. See §21 of v0.6 for the expected list.

## Reading order

1. Executive Summary (§1) and Product Vision (§3) for what SwarmKit is.
2. Core Concepts (§5), Skills (§6), Separation of Powers (§8) for the mental model.
3. System Architecture (§9), Runtime Architecture (§14) for where code lives.
4. Release Plan (§20) for scope and sequencing.
