"""Tests for Wave 3: Sterling structured researchers + authoring output_schema.

Verifies that Sterling research workers get structured output by default,
document-writer opts out, and the authoring prompt mentions output_schema.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from swarmkit_runtime.authoring._prompts import get_system_prompt
from swarmkit_schema import validate

_STERLING_ARCHETYPES = Path("examples/sterling-oms/workspace/archetypes")


class TestSterlingArchetypes:
    def test_document_writer_opts_out(self) -> None:
        data = yaml.safe_load((_STERLING_ARCHETYPES / "document-writer.yaml").read_text())
        validate("archetype", data)
        assert data["defaults"]["output_schema"] is None

    def test_research_workers_have_no_explicit_schema(self) -> None:
        """Research workers don't set output_schema — they get the platform default."""
        research_archetypes = [
            "jira-researcher.yaml",
            "config-analyst.yaml",
            "docs-researcher.yaml",
            "sterling-developer.yaml",
            "log-analyst.yaml",
        ]
        for name in research_archetypes:
            path = _STERLING_ARCHETYPES / name
            if not path.exists():
                continue
            data = yaml.safe_load(path.read_text())
            validate("archetype", data)
            assert data["role"] == "worker"
            assert "output_schema" not in data.get("defaults", {}), (
                f"{name} should not set output_schema — workers get structured output by default"
            )

    def test_all_sterling_archetypes_validate(self) -> None:
        for path in sorted(_STERLING_ARCHETYPES.glob("*.yaml")):
            data = yaml.safe_load(path.read_text())
            validate("archetype", data)


class TestAuthoringPrompt:
    def test_archetype_prompt_mentions_output_schema(self) -> None:
        prompt = get_system_prompt("archetype")
        assert "output_schema" in prompt
        assert "structured output" in prompt.lower() or "structured JSON" in prompt

    def test_archetype_prompt_shows_null_opt_out(self) -> None:
        prompt = get_system_prompt("archetype")
        assert "output_schema: null" in prompt

    def test_archetype_prompt_has_writer_example(self) -> None:
        prompt = get_system_prompt("archetype")
        assert "report-writer" in prompt or "document writer" in prompt.lower()
