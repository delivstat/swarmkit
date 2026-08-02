#!/usr/bin/env python
"""Demo: what a `post_output` decision skill actually does to a topology's answer.

    uv run python examples/output-validation/demo.py

No API key and no database. The governance provider here is a stub with a scripted verdict, so the
sequence is deterministic and the point is visible: a failed check does not reject the run — it
hands the agent revision instructions and asks again, and if the retries run out the answer is
returned ANNOTATED rather than dropped.

That last part is the one people design against wrongly, which is why this prints it.
See docs/site/guides/validating-topology-output.md.
"""

from __future__ import annotations

import asyncio
from typing import Any

from swarmkit_runtime.governance import DecisionSkillBinding, DecisionSkillResult
from swarmkit_runtime.langgraph_compiler._decision_gate import evaluate_post_output

BOLD, DIM, OFF = "\033[1m", "\033[2m", "\033[0m"


def hr(title: str) -> None:
    print(f"\n{BOLD}{title}{OFF}")


class ScriptedJudge:
    """A decision skill that fails until the agent removes the unsupported figure."""

    def __init__(self) -> None:
        self.evaluations = 0

    async def evaluate_decision_skill(
        self,
        *,
        skill_id: str,
        trigger: str,
        agent_id: str,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> DecisionSkillResult:
        self.evaluations += 1
        if self.evaluations == 1:
            seen = (context or {}).get("config")
            print(f"  {DIM}judge #{self.evaluations}: config seen = {seen}{OFF}")
        grounded = "40%" not in content
        return DecisionSkillResult(
            skill_id=skill_id,
            verdict="pass" if grounded else "fail",
            confidence=0.9,
            reasoning=(
                "ok"
                if grounded
                else "The 40% figure appears in no cited source. Remove it, or cite the "
                "measurement it came from."
            ),
            flagged_items=[] if grounded else ["response time improved 40%"],
        )


async def main() -> None:
    binding = DecisionSkillBinding(
        id="output-conformance",
        trigger="post_output",
        scope="coordinator",
        config={"max_retries": 2},
    )

    hr("1. The agent revises, and the run returns a clean answer")
    judge = ScriptedJudge()
    revisions = 0

    async def cooperative_retry(feedback: str) -> str:
        nonlocal revisions
        revisions += 1
        print(
            f"  {DIM}retry {revisions}: agent told → {feedback.strip().splitlines()[-1][:78]}{OFF}"
        )
        return "Latency improved, per the benchmark in the cited report."

    output, results = await evaluate_post_output(
        agent_id="coordinator",
        output="Latency improved 40%, a substantial gain.",
        bindings=[binding],
        governance=judge,  # type: ignore[arg-type]
        retry_fn=cooperative_retry,
    )
    print(f"  verdict: {results[0].verdict}  ({judge.evaluations} judge calls, {revisions} retry)")
    print(f"  returned: {output}")

    hr("2. The agent will not fix it — retries run out")
    judge = ScriptedJudge()
    attempts = 0

    async def stubborn_retry(_feedback: str) -> str:
        nonlocal attempts
        attempts += 1
        return "Latency improved 40%, I stand by this."

    output, results = await evaluate_post_output(
        agent_id="coordinator",
        output="Latency improved 40%, a substantial gain.",
        bindings=[binding],
        governance=judge,  # type: ignore[arg-type]
        retry_fn=stubborn_retry,
    )
    print(f"  {attempts} retries (config said max_retries: 2 — the default is 4)")
    print(f"  verdict: {results[0].verdict}")
    print(f"  returned:\n{DIM}{output}{OFF}")
    print("\n  The answer came BACK, flagged. A decision skill annotates; it does not block.")
    print("  If you need a hard stop, that is an approval gate, not a decision skill.")

    hr("3. scope decides how many judge calls a run costs")
    wide = DecisionSkillBinding(id="output-conformance", trigger="post_output")  # scope '*'
    for agent in ("coordinator", "researcher", "writer"):
        fires_narrow = binding.applies_to(agent)
        fires_wide = wide.applies_to(agent)
        print(f"  {agent:<14} scope 'coordinator': {fires_narrow!s:<5}  scope '*': {fires_wide}")
    print("\n  '*' is the default, and it fires after EVERY agent — N judge calls, not one.")


if __name__ == "__main__":
    asyncio.run(main())
