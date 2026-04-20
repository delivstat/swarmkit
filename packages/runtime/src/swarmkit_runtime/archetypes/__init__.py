"""Archetype registry and instantiation (design §13).

Archetypes are pre-configured agent definitions (role + default model + prompt
template + skill set + default IAM). Topologies reference archetypes by ID and
override fields per-agent.
"""
