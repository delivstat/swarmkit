"""Multi-persona panel aggregation for composed decision skills.

Implements ``parallel-consensus`` and ``sequential`` strategies.
See ``design/details/decision-skills.md`` §Multi-persona panels.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from swarmkit_runtime.model_providers._registry import ModelProviderProtocol
from swarmkit_runtime.skills import ResolvedSkill

from ._skill_executor import execute_skill


async def execute_panel(
    panel_skill: ResolvedSkill,
    constituent_skills: list[ResolvedSkill],
    *,
    input_text: str,
    model_provider: ModelProviderProtocol,
    model_name: str,
) -> str:
    """Execute a composed decision skill panel and aggregate verdicts."""
    impl = panel_skill.raw.implementation
    strategy = (
        impl.get("strategy", "parallel-consensus")
        if isinstance(impl, dict)
        else "parallel-consensus"
    )

    if strategy == "sequential":
        return await _sequential(constituent_skills, input_text, model_provider, model_name)
    return await _parallel_consensus(constituent_skills, input_text, model_provider, model_name)


async def _parallel_consensus(
    skills: list[ResolvedSkill],
    input_text: str,
    model_provider: ModelProviderProtocol,
    model_name: str,
) -> str:
    """All judges run in parallel. Majority verdict wins."""
    tasks = [
        execute_skill(
            s, input_text=input_text, model_provider=model_provider, model_name=model_name
        )
        for s in skills
    ]
    results = await asyncio.gather(*tasks)

    votes = _parse_votes(skills, results)
    return json.dumps(_aggregate_majority(votes))


async def _sequential(
    skills: list[ResolvedSkill],
    input_text: str,
    model_provider: ModelProviderProtocol,
    model_name: str,
) -> str:
    """Judges run in order. First fail stops the chain."""
    votes: list[dict[str, Any]] = []
    for skill in skills:
        result = await execute_skill(
            skill, input_text=input_text, model_provider=model_provider, model_name=model_name
        )
        vote = _parse_single_vote(skill.id, result)
        votes.append(vote)
        if vote.get("verdict") == "fail":
            return json.dumps(_aggregate_first_fail(votes))

    return json.dumps(_aggregate_majority(votes))


def _parse_votes(skills: list[ResolvedSkill], results: list[str]) -> list[dict[str, Any]]:
    votes: list[dict[str, Any]] = []
    for skill, result in zip(skills, results, strict=True):
        votes.append(_parse_single_vote(skill.id, result))
    return votes


def _parse_single_vote(judge_id: str, result: str) -> dict[str, Any]:
    try:
        parsed = json.loads(result)
        return {
            "judge": judge_id,
            "verdict": parsed.get("verdict", "unknown"),
            "confidence": parsed.get("confidence", 0.0),
            "reasoning": parsed.get("reasoning", ""),
        }
    except (json.JSONDecodeError, TypeError):
        return {
            "judge": judge_id,
            "verdict": "error",
            "confidence": 0.0,
            "reasoning": result[:200],
        }


def _aggregate_majority(votes: list[dict[str, Any]]) -> dict[str, Any]:
    pass_count = sum(1 for v in votes if v.get("verdict") == "pass")
    fail_count = sum(1 for v in votes if v.get("verdict") == "fail")
    verdict = "pass" if pass_count > fail_count else "fail"

    agreeing = [v for v in votes if v.get("verdict") == verdict]
    confidence = (
        sum(v.get("confidence", 0.0) for v in agreeing) / len(agreeing) if agreeing else 0.0
    )

    reasons = [f"[{v['judge']}]: {v.get('reasoning', '')}" for v in votes]

    return {
        "verdict": verdict,
        "confidence": round(confidence, 3),
        "reasoning": " | ".join(reasons),
        "panel_votes": votes,
    }


def _aggregate_first_fail(votes: list[dict[str, Any]]) -> dict[str, Any]:
    last = votes[-1]
    return {
        "verdict": "fail",
        "confidence": last.get("confidence", 0.0),
        "reasoning": f"Sequential fail at {last['judge']}: {last.get('reasoning', '')}",
        "panel_votes": votes,
    }
