"""Gate Validator MCP Server — file-based schema validation gates.

Drop a JSON Schema file into the gates directory and it becomes a
validation gate. Agents' structured output is validated against
the schema — no external service, no API keys, no config.

Usage:
  python -m swarmkit_runtime.gate_validator
  # or via workspace.yaml:
  # - id: gate-validator
  #   transport: stdio
  #   command: ["uv", "run", "python", "-m", "swarmkit_runtime.gate_validator"]

Environment:
  SWARMKIT_GATES_DIR — path to gates directory (default: ./gates)

Gate files:
  gates/
    sterling-findings.json    → gate_id: "sterling-findings"
    pipeline-config.yaml      → gate_id: "pipeline-config"

Each file is a JSON Schema. The validate_gate tool validates
content against it and returns a decision skill result.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from mcp.server.fastmcp import FastMCP

server = FastMCP("gate-validator")

_gates_dir: Path | None = None
_gate_cache: dict[str, dict[str, Any]] = {}


def _get_gates_dir() -> Path:
    global _gates_dir  # noqa: PLW0603
    if _gates_dir is None:
        _gates_dir = Path(os.environ.get("SWARMKIT_GATES_DIR", "./gates")).resolve()
    return _gates_dir


def _load_gate(gate_id: str) -> dict[str, Any] | None:
    if gate_id in _gate_cache:
        return _gate_cache[gate_id]

    gates_dir = _get_gates_dir()
    for ext in (".json", ".yaml", ".yml"):
        path = gates_dir / f"{gate_id}{ext}"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            schema: dict[str, Any] = json.loads(text) if ext == ".json" else yaml.safe_load(text)
            _gate_cache[gate_id] = schema
            return schema
    return None


def _discover_gates() -> list[dict[str, str]]:
    gates_dir = _get_gates_dir()
    if not gates_dir.exists():
        return []
    gates: list[dict[str, str]] = []
    for path in sorted(gates_dir.iterdir()):
        if path.suffix in (".json", ".yaml", ".yml"):
            gate_id = path.stem
            try:
                text = path.read_text(encoding="utf-8")
                schema = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
                desc = schema.get("description", "") or schema.get("title", "")
            except Exception:
                desc = "(failed to parse)"
            gates.append({"gate_id": gate_id, "description": desc, "path": str(path)})
    return gates


@server.tool()
def list_gates() -> str:
    """List all available validation gates.

    Scans the gates directory for JSON/YAML schema files.
    Each file becomes a gate that can validate agent output.
    """
    gates = _discover_gates()
    if not gates:
        gates_dir = _get_gates_dir()
        return (
            f"No gates found in {gates_dir}. "
            f"Create a JSON Schema file (e.g. gates/my-gate.json) "
            f"to define a validation gate."
        )
    return json.dumps(gates, indent=2)


@server.tool()
def validate_gate(gate_id: str, content: str) -> str:
    """Validate content against a gate's JSON Schema.

    Args:
        gate_id: The gate to validate against (filename without extension).
        content: JSON string to validate. Must be parseable as JSON.

    Returns:
        Decision skill result: {verdict, confidence, reasoning, flagged_items}
    """
    schema = _load_gate(gate_id)
    if schema is None:
        available = [g["gate_id"] for g in _discover_gates()]
        return json.dumps(
            {
                "verdict": "fail",
                "confidence": 1.0,
                "reasoning": f"Gate '{gate_id}' not found. Available: {available}",
                "flagged_items": [],
            }
        )

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        return json.dumps(
            {
                "verdict": "fail",
                "confidence": 1.0,
                "reasoning": f"Content is not valid JSON: {exc}",
                "flagged_items": [],
            }
        )

    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(parsed))

    if not errors:
        return json.dumps(
            {
                "verdict": "pass",
                "confidence": 1.0,
                "reasoning": f"Content validates against gate '{gate_id}'.",
                "flagged_items": [],
            }
        )

    flagged = []
    for err in errors[:10]:
        path = ".".join(str(p) for p in err.absolute_path) or "(root)"
        flagged.append(f"{path}: {err.message}")

    return json.dumps(
        {
            "verdict": "fail",
            "confidence": 1.0,
            "reasoning": f"{len(errors)} validation error(s) against gate '{gate_id}'.",
            "flagged_items": flagged,
        }
    )


def set_gates_dir(path: Path) -> None:
    """Override the gates directory (for testing)."""
    global _gates_dir  # noqa: PLW0603
    _gates_dir = path.resolve()
    _gate_cache.clear()


def run_server() -> None:
    """Run the gate-validator MCP server."""
    server.run()
