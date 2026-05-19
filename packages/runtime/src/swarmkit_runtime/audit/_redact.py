"""Audit redaction — applies per-skill and workspace-level audit controls.

Skills declare their audit preferences via the `audit:` block in YAML:

    audit:
      log_inputs: summary    # full | summary | none
      log_outputs: full
      redact: ["$.password", "$.api_key"]

Category defaults (when no audit block is declared):
  - capability: log_inputs=summary, log_outputs=summary
  - decision: log_inputs=summary, log_outputs=full
  - coordination: log_inputs=summary, log_outputs=summary
  - persistence: log_inputs=summary, log_outputs=none

Workspace-level `audit.level` (minimal/standard/detailed) clamps
per-skill settings.

See design/details/human-interaction-model.md.
"""

from __future__ import annotations

from typing import Any

from swarmkit_runtime.governance import redact_json_pointers, summarize_value

_CATEGORY_DEFAULTS: dict[str, dict[str, str]] = {
    "capability": {"log_inputs": "summary", "log_outputs": "summary"},
    "decision": {"log_inputs": "summary", "log_outputs": "full"},
    "coordination": {"log_inputs": "summary", "log_outputs": "summary"},
    "persistence": {"log_inputs": "summary", "log_outputs": "none"},
}

_LEVEL_CLAMP: dict[str, dict[str, str]] = {
    "minimal": {"log_inputs": "none", "log_outputs": "none"},
    "standard": {"log_inputs": "summary", "log_outputs": "summary"},
    "detailed": {"log_inputs": "full", "log_outputs": "full"},
}


def apply_audit_policy(
    data: dict[str, Any],
    *,
    field: str,
    log_level: str,
    redact_paths: list[str] | None = None,
) -> dict[str, Any] | None:
    """Apply audit policy to a data dict.

    Returns None if log_level is 'none', summarized dict if 'summary',
    or the full dict (with redaction) if 'full'.
    """
    if log_level == "none":
        return None

    if redact_paths:
        data = redact_json_pointers(data, redact_paths)

    if log_level == "summary":
        return {k: summarize_value(v) for k, v in data.items()}

    return data


def resolve_audit_config(
    skill_audit: Any | None,
    skill_category: str | None,
    workspace_level: str | None = None,
) -> tuple[str, str, list[str]]:
    """Resolve the effective audit config for a skill.

    Returns (log_inputs, log_outputs, redact_paths).

    Resolution: workspace level clamps > skill audit block > category defaults.
    """
    raw_cat = skill_category
    if raw_cat is not None and hasattr(raw_cat, "value"):
        category = str(raw_cat.value)
    else:
        category = str(raw_cat or "capability")
    defaults = _CATEGORY_DEFAULTS.get(category, _CATEGORY_DEFAULTS["capability"])

    log_inputs = defaults["log_inputs"]
    log_outputs = defaults["log_outputs"]
    redact_paths: list[str] = []

    if skill_audit is not None:
        if hasattr(skill_audit, "log_inputs") and skill_audit.log_inputs:
            raw = skill_audit.log_inputs
            log_inputs = raw.value if hasattr(raw, "value") else str(raw)
        if hasattr(skill_audit, "log_outputs") and skill_audit.log_outputs:
            raw = skill_audit.log_outputs
            log_outputs = raw.value if hasattr(raw, "value") else str(raw)
        if hasattr(skill_audit, "redact") and skill_audit.redact:
            redact_paths = list(skill_audit.redact)

    if workspace_level and workspace_level in _LEVEL_CLAMP:
        clamp = _LEVEL_CLAMP[workspace_level]
        log_inputs = _clamp_level(log_inputs, clamp["log_inputs"])
        log_outputs = _clamp_level(log_outputs, clamp["log_outputs"])

    return log_inputs, log_outputs, redact_paths


def _clamp_level(current: str, max_level: str) -> str:
    """Clamp a log level to not exceed the workspace max."""
    order = {"none": 0, "summary": 1, "full": 2}
    current_val = order.get(current, 1)
    max_val = order.get(max_level, 1)
    if current_val > max_val:
        return max_level
    return current
