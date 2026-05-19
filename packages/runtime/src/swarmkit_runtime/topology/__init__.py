"""Topology loading, validation, and resolution (design §10, §14.3).

Responsibilities:
  - Parse YAML/JSON topology files
  - Validate against `swarmkit-schema`
  - Resolve archetype references (merge archetype defaults into agent defs)
  - Resolve skill references (each skill validated for schema + availability)
  - Produce a fully-resolved `ResolvedTopology` that the compiler consumes
"""
