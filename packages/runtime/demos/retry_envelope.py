"""Demo: what a retried harness run actually receives.

Bug 13. With decision skills running on harness executors, the first revision was refused by the
agent on safety grounds — it inspected its worktree, found no trace of the "prior turn" it was being
shown, and declined. It was right to: a harness retry is a new process with no memory of the earlier
turn, and the content arrived unattributed, undelimited, and carrying a `[harness:claude-code]`
prefix the agent never wrote.

    uv run python packages/runtime/demos/retry_envelope.py
"""

from __future__ import annotations

from swarmkit_runtime.review._prior_output import render_retry_statement

TASK = "Design the RF screens for the pick-confirm flow."
DRAFT = "# WMS Design\n\n## PGM hold / RF screens\n\nThe PGM screen confirms a pick against a tag."
CRITIQUE = "[spec-conformance]: output is markdown; a JSON object matching the spec is required"


def _old_envelope() -> str:
    """What 1.145.1 sent: the task, then a bare instruction referring to work not present — or,
    where the draft did arrive via upstream artifacts, prefixed and unmarked."""
    return (
        f"{TASK}\n\n"
        f"[harness:claude-code] {DRAFT}\n\n"
        "A governance review of your previous attempt requires changes before this can be "
        "accepted. Address this feedback and produce the COMPLETE corrected result:\n"
        f"Revise your output to address the following issues:\n\n{CRITIQUE}"
    )


def main() -> None:
    print("\n  BEFORE — refused as prompt injection")
    print("  " + "─" * 76)
    for line in _old_envelope().splitlines():
        print(f"    {line}")
    print("  " + "─" * 76)
    print(
        "    Nothing says who wrote the block, nothing bounds it, and `[harness:claude-code]`\n"
        "    is a provenance claim the agent can prove false — it never wrote it.\n"
    )

    print("  AFTER — attributed, delimited, unprefixed")
    print("  " + "─" * 76)
    for line in render_retry_statement(
        TASK, DRAFT, CRITIQUE, agent_id="designer", round_=1, source="decision-skill"
    ).splitlines():
        print(f"    {line}")
    print("  " + "─" * 76)
    print(
        "\n    The runtime states it is supplying the draft, names the author and the round,\n"
        "    bounds the content so an imperative inside it cannot read as an instruction, and\n"
        "    keeps the critique in its own block so the agent can tell what to change from\n"
        "    what is telling it to change.\n"
    )


if __name__ == "__main__":
    main()
