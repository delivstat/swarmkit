"""Gate Validator MCP Server — local file-based validation gates.

Each JSON/YAML file in the gates directory is a validation gate.
The filename (minus extension) is the gate_id. The file contains
a JSON Schema that agent output is validated against.

No external service needed — works offline, zero config.
"""
