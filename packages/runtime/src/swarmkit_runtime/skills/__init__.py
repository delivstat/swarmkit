"""Skill registry and category semantics (design §6).

Four categories:
  - capability   — give the agent a new ability (MCP tools, retrieval, API calls)
  - decision     — let the agent evaluate or judge (validation gates, LLM judges)
  - coordination — let the agent communicate or hand off (A2A, escalations)
  - persistence  — let the agent remember or record (KB writes, audit, queues)

Skills are data (YAML/JSON) referenced by ID; this module loads, resolves, and
dispatches them at runtime with category-appropriate semantics.
"""
