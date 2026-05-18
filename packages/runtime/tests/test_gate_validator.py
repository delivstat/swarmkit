"""Tests for the gate-validator MCP server.

Verifies gate discovery, schema validation, and decision skill result format.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from swarmkit_runtime.gate_validator._server import (
    _discover_gates,
    _load_gate,
    list_gates,
    set_gates_dir,
    validate_gate,
)
from swarmkit_schema import validate as validate_schema


class TestGateDiscovery:
    def test_discovers_json_gates(self, tmp_path: Path) -> None:
        gate_file = tmp_path / "my-gate.json"
        gate_file.write_text(
            json.dumps(
                {
                    "type": "object",
                    "title": "Test Gate",
                    "required": ["name"],
                    "properties": {"name": {"type": "string"}},
                }
            )
        )
        set_gates_dir(tmp_path)
        gates = _discover_gates()
        assert len(gates) == 1
        assert gates[0]["gate_id"] == "my-gate"
        assert gates[0]["description"] == "Test Gate"

    def test_discovers_yaml_gates(self, tmp_path: Path) -> None:
        gate_file = tmp_path / "check.yaml"
        gate_file.write_text(
            yaml.dump(
                {
                    "type": "object",
                    "description": "YAML gate",
                    "properties": {"x": {"type": "integer"}},
                }
            )
        )
        set_gates_dir(tmp_path)
        gates = _discover_gates()
        assert len(gates) == 1
        assert gates[0]["gate_id"] == "check"

    def test_empty_directory(self, tmp_path: Path) -> None:
        set_gates_dir(tmp_path)
        gates = _discover_gates()
        assert gates == []

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        set_gates_dir(tmp_path / "does-not-exist")
        gates = _discover_gates()
        assert gates == []


class TestLoadGate:
    def test_loads_json(self, tmp_path: Path) -> None:
        schema = {"type": "object", "required": ["x"]}
        (tmp_path / "test.json").write_text(json.dumps(schema))
        set_gates_dir(tmp_path)
        loaded = _load_gate("test")
        assert loaded == schema

    def test_loads_yaml(self, tmp_path: Path) -> None:
        schema = {"type": "object", "required": ["y"]}
        (tmp_path / "test.yaml").write_text(yaml.dump(schema))
        set_gates_dir(tmp_path)
        loaded = _load_gate("test")
        assert loaded is not None
        assert loaded["required"] == ["y"]

    def test_missing_gate(self, tmp_path: Path) -> None:
        set_gates_dir(tmp_path)
        assert _load_gate("nonexistent") is None


class TestValidateGate:
    def test_pass(self, tmp_path: Path) -> None:
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }
        (tmp_path / "simple.json").write_text(json.dumps(schema))
        set_gates_dir(tmp_path)

        result = json.loads(validate_gate("simple", '{"name": "test"}'))
        assert result["verdict"] == "pass"
        assert result["confidence"] == 1.0

    def test_fail_missing_required(self, tmp_path: Path) -> None:
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }
        (tmp_path / "simple.json").write_text(json.dumps(schema))
        set_gates_dir(tmp_path)

        result = json.loads(validate_gate("simple", '{"other": "value"}'))
        assert result["verdict"] == "fail"
        assert len(result["flagged_items"]) >= 1
        assert "name" in result["flagged_items"][0].lower()

    def test_fail_wrong_type(self, tmp_path: Path) -> None:
        schema = {
            "type": "object",
            "properties": {"count": {"type": "integer"}},
        }
        (tmp_path / "typed.json").write_text(json.dumps(schema))
        set_gates_dir(tmp_path)

        result = json.loads(validate_gate("typed", '{"count": "not-a-number"}'))
        assert result["verdict"] == "fail"

    def test_gate_not_found(self, tmp_path: Path) -> None:
        set_gates_dir(tmp_path)
        result = json.loads(validate_gate("missing", "{}"))
        assert result["verdict"] == "fail"
        assert "not found" in result["reasoning"].lower()

    def test_invalid_json_content(self, tmp_path: Path) -> None:
        schema = {"type": "object"}
        (tmp_path / "any.json").write_text(json.dumps(schema))
        set_gates_dir(tmp_path)

        result = json.loads(validate_gate("any", "not json at all"))
        assert result["verdict"] == "fail"
        assert "not valid JSON" in result["reasoning"]

    def test_findings_gate(self, tmp_path: Path) -> None:
        schema = {
            "type": "object",
            "required": ["findings"],
            "properties": {
                "findings": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["fact", "source"],
                        "properties": {
                            "fact": {"type": "string", "minLength": 5},
                            "source": {"type": "string", "minLength": 1},
                        },
                    },
                }
            },
        }
        (tmp_path / "findings-check.json").write_text(json.dumps(schema))
        set_gates_dir(tmp_path)

        good = json.dumps({"findings": [{"fact": "Config has retries=3", "source": "cdt:search"}]})
        result = json.loads(validate_gate("findings-check", good))
        assert result["verdict"] == "pass"

        bad = json.dumps({"findings": [{"fact": "X", "source": ""}]})
        result = json.loads(validate_gate("findings-check", bad))
        assert result["verdict"] == "fail"


class TestListGates:
    def test_lists_gates(self, tmp_path: Path) -> None:
        (tmp_path / "a.json").write_text('{"type": "object", "title": "Gate A"}')
        (tmp_path / "b.yaml").write_text("type: object\ntitle: Gate B")
        set_gates_dir(tmp_path)

        result = list_gates()
        parsed = json.loads(result)
        assert len(parsed) == 2
        ids = {g["gate_id"] for g in parsed}
        assert ids == {"a", "b"}

    def test_empty_message(self, tmp_path: Path) -> None:
        set_gates_dir(tmp_path)
        result = list_gates()
        assert "No gates found" in result


class TestReferenceSkill:
    def test_gate_validator_skill_validates(self) -> None:
        path = Path("reference/skills/gate-validator.yaml")
        data = yaml.safe_load(path.read_text())
        validate_schema("skill", data)
        assert data["category"] == "decision"
        assert data["implementation"]["type"] == "mcp_tool"
        assert data["implementation"]["server"] == "gate-validator"

    def test_example_gate_schemas_are_valid_json_schema(self) -> None:
        gates_dir = Path("docs/examples/gates")
        for path in sorted(gates_dir.glob("*.json")):
            data = json.loads(path.read_text())
            assert "type" in data, f"{path.name} missing 'type'"
            assert "properties" in data or "items" in data, f"{path.name} has no shape"
