---
title: Design directory
description: Authoritative architecture documents. Markdown is canonical; docx archived.
tags: [meta, docs]
status: active
---

# Design

Authoritative architecture documents for Swael.

- **`Swael-Design-v0.6.md`** — **canonical source of truth.** Edit this file directly.
- **`details/`** — per-feature design notes. One file per feature, produced before implementation; see `details/README.md` for the template.
- **`IMPLEMENTATION-PLAN.md`** — phased roadmap to v1.0.
- **`archive/Swael-Design-v0.6.docx`** — the original Word document. Kept for historical reference; **not authoritative.** Do not edit — changes land on the `.md` file.

## Why the markdown is canonical

Swael's documentation is consumed primarily by LLMs (on behalf of users, reviewers, and contributors). A binary `.docx` is hostile to that: it requires a script to extract readable text, it diff-s poorly in PRs, and it's invisible to retrieval tooling (`llms.txt`, GitHub search, etc.). The markdown is the artifact every consumer actually reads; treating it as source removes the extraction step and eliminates drift between source and readable copy.

If a Word-format export is ever needed, generate it from the markdown (e.g. via `pandoc design/Swael-Design-v0.6.md -o out.docx`). The reverse conversion (`docx → md`) is now a one-time historical thing and does not have a checked-in script.

## Reading order

1. Executive Summary (§1) and Product Vision (§3) for what Swael is.
2. Core Concepts (§5), Skills (§6), Separation of Powers (§8) for the mental model.
3. System Architecture (§9), Runtime Architecture (§14) for where code lives.
4. Release Plan (§20) for scope and sequencing.
