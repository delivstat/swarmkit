"""Topology → LangGraph `StateGraph` compilation (design §14.3).

Pipeline (design §14.3):
  1. Parse + validate topology against schema
  2. Resolve archetype references and merge configuration
  3. Resolve skill references; validate schemas and availability
  4. Construct LangGraph nodes for each agent
  5. Construct edges from hierarchy and coordination-skill definitions
  6. Wire decision-skill invocations as evaluation nodes
  7. Wire persistence-skill invocations (audit, KB writes, gap logging)
  8. Configure checkpointing, retries, HITL interrupts
  9. Compile the graph

The `eject` command (§14.4) reuses this pipeline to emit standalone code.
"""
