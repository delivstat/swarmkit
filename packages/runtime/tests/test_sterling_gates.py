"""Tests for Sterling workspace gate validation schemas.

Verifies that gate schemas are valid JSON Schema, the workspace
wires gate-validator correctly, and the schemas enforce the
expected constraints on agent output.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml
from swarmkit_schema import validate as validate_schema

_STERLING_WS = Path("examples/sterling-oms/workspace")
_GATES_DIR = _STERLING_WS / "gates"


class TestSterlingGateSchemas:
    def test_all_gate_schemas_are_valid(self) -> None:
        for path in sorted(_GATES_DIR.glob("*.json")):
            data = json.loads(path.read_text())
            assert "type" in data, f"{path.name} missing 'type'"
            assert "properties" in data, f"{path.name} missing 'properties'"

    def test_sterling_findings_rejects_empty_source(self) -> None:
        schema = json.loads((_GATES_DIR / "sterling-findings.json").read_text())
        bad = {"findings": [{"fact": "Something interesting found", "source": ""}]}
        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors(bad))
        assert len(errors) > 0

    def test_sterling_findings_rejects_short_fact(self) -> None:
        schema = json.loads((_GATES_DIR / "sterling-findings.json").read_text())
        bad = {"findings": [{"fact": "short", "source": "cdt:search"}]}
        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors(bad))
        assert len(errors) > 0

    def test_sterling_findings_accepts_valid(self) -> None:
        schema = json.loads((_GATES_DIR / "sterling-findings.json").read_text())
        good = {
            "findings": [
                {
                    "fact": "Pipeline RETURN_ORDER has 5 conditions in hub rules",
                    "source": "sterling-config:get-pipeline",
                    "confidence": "observed",
                }
            ],
            "not_found": ["cancellation flow for RTO"],
        }
        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors(good))
        assert errors == []

    def test_code_findings_requires_file_path(self) -> None:
        schema = json.loads((_GATES_DIR / "code-findings.json").read_text())
        bad = {"findings": [{"fact": "Method processReturn handles return", "source": "grep"}]}
        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors(bad))
        assert len(errors) > 0

    def test_code_findings_accepts_with_file_path(self) -> None:
        schema = json.loads((_GATES_DIR / "code-findings.json").read_text())
        good = {
            "findings": [
                {
                    "fact": "Method processReturn handles return orders",
                    "source": "project-code:read-file",
                    "file_path": "src/com/acme/ReturnProcessor.java",
                    "line_range": "42-68",
                }
            ]
        }
        validator = jsonschema.Draft202012Validator(schema)
        errors = list(validator.iter_errors(good))
        assert errors == []


class TestSterlingWorkspaceGateConfig:
    def test_workspace_has_gate_validator_server(self) -> None:
        data = yaml.safe_load((_STERLING_WS / "workspace.yaml").read_text())
        server_ids = [s["id"] for s in data["mcp_servers"]]
        assert "gate-validator" in server_ids

    def test_workspace_has_gate_validator_binding(self) -> None:
        data = yaml.safe_load((_STERLING_WS / "workspace.yaml").read_text())
        bindings = data["governance"]["decision_skills"]
        gate_binding = next((b for b in bindings if b["id"] == "gate-validator"), None)
        assert gate_binding is not None
        assert gate_binding["trigger"] == "post_output"
        assert gate_binding["config"]["gate_id"] == "sterling-findings"

    def test_grounding_verifier_disabled(self) -> None:
        data = yaml.safe_load((_STERLING_WS / "workspace.yaml").read_text())
        bindings = data["governance"]["decision_skills"]
        grounding = next((b for b in bindings if b["id"] == "grounding-verifier"), None)
        assert grounding is not None
        assert grounding["required"] is False

    def test_gate_validator_skill_exists(self) -> None:
        skill_path = _STERLING_WS / "skills" / "gate-validator.yaml"
        data = yaml.safe_load(skill_path.read_text())
        validate_schema("skill", data)
        assert data["implementation"]["server"] == "gate-validator"
