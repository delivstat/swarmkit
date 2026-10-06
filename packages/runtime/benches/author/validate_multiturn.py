#!/usr/bin/env python3
"""Multi-turn validation of the bundled authoring agents (#1045).

Where the sibling `run.py` measures a *one-shot* ``swarmkit run`` invocation (and so
reliably produces zero files because the author's charter proposes a plan first), this
script drives the full propose → approve → write cycle via the CLI REPL path. Each
case is a short conversation with scripted approval turns piped on stdin.

Per mode, the harness:

1. Prepares a throwaway target workspace with the fixture files the prompt wants.
2. Invokes ``swarmkit author <mode> <ws>`` with stdin feeding: the requirement,
   two approval turns ("yes, create them" / "do it"), and ``/exit``.
3. After the subprocess exits, scans the target workspace for files that landed
   under topologies/, archetypes/, skills/, funnels/, schemas/, policies/.
4. Validates the resulting workspace with ``swarmkit validate``.

Pass = at least one file landed AND the workspace validates.

Writes ``results-multiturn/<mode-id>/{workspace/, transcript.log, meta.json}`` and a
headline ``report.md``.

Needs OPENROUTER_API_KEY in the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results-multiturn"
TOPLEVEL_DIRS = ("topologies", "skills", "archetypes", "funnels", "triggers", "schemas", "policies")


@dataclass
class Case:
    mode: str
    id: str
    requirement: str
    fixture_files: dict[str, str]
    approvals: list[str]
    extra_turns: list[str]


# One case per mode. Approvals default to a short "yes" + a stronger "write it".
# Each case ends with /exit to shut the REPL down cleanly.
_YES = ["yes, create them", "do it"]

CASES: list[Case] = [
    Case(
        mode="topology",
        id="url-brief",
        requirement=(
            "A single-agent topology called url-brief that takes a URL and returns a "
            "3-bullet summary. Root agent only, no children. Use the openrouter "
            "provider with moonshotai/kimi-k2-0905. Output schema: an object with "
            "{url: string, bullets: array of 3 strings}."
        ),
        fixture_files={},
        approvals=list(_YES),
        extra_turns=[],
    ),
    Case(
        mode="skill",
        id="sentiment-check",
        requirement=(
            "A decision skill called sentiment-check that reads a text and returns "
            "{verdict: enum[positive,neutral,negative], confidence: number 0-1, "
            "reasoning: string}. Implementation llm_prompt."
        ),
        fixture_files={},
        approvals=list(_YES),
        extra_turns=[],
    ),
    Case(
        mode="archetype",
        id="pr-reviewer",
        requirement=(
            "An archetype called pr-reviewer for a worker role that reviews pull "
            "requests. Default model openrouter/moonshotai/kimi-k2-0905, "
            "temperature 0.2. Include a short system prompt and no skills."
        ),
        fixture_files={},
        approvals=list(_YES),
        extra_turns=[],
    ),
    Case(
        mode="mcp-server",
        id="filesystem-bundle",
        requirement=(
            "Register the filesystem bundle from the swarmkit-skills catalogue. "
            "Use the Path 0 install command."
        ),
        fixture_files={},
        approvals=list(_YES),
        extra_turns=[],
    ),
    Case(
        mode="init",
        id="minimal",
        requirement=(
            "Scaffold a minimal workspace called hello-swarm. One topology called "
            "echo that returns the user's input unchanged. Governance mock. SQLite "
            "storage."
        ),
        fixture_files={},
        approvals=list(_YES),
        extra_turns=[],
    ),
    Case(
        mode="funnel",
        id="refund-approval",
        requirement=(
            "A funnel called refund-approval. Scope refund:approve, role "
            "support-lead, quorum all, min_distinct_approvers 1. No schema validate "
            "block — pure human gate."
        ),
        fixture_files={},
        approvals=list(_YES),
        extra_turns=[],
    ),
]


def _seed_workspace(target: Path, fixture: dict[str, str]) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / "topologies").mkdir(exist_ok=True)
    for rel, body in fixture.items():
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
    ws_file = target / "workspace.yaml"
    if not ws_file.exists():
        ws_file.write_text(
            "apiVersion: swarmkit/v1\n"
            "kind: Workspace\n"
            "metadata:\n"
            "  id: multiturn-bench\n"
            "  name: Multi-turn bench\n",
            encoding="utf-8",
        )


def _list_files(target: Path) -> list[str]:
    out: list[str] = []
    for sub in TOPLEVEL_DIRS:
        folder = target / sub
        if folder.is_dir():
            out.extend(sorted(str(p.relative_to(target)) for p in folder.glob("*")))
    return out


def _bin_dir() -> Path:
    return Path(sys.executable).parent


def _run_case(case: Case, out_dir: Path, timeout_s: int) -> dict[str, object]:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "workspace"
    if target.exists():
        shutil.rmtree(target)
    _seed_workspace(target, case.fixture_files)

    before = set(_list_files(target))

    turns = [case.requirement, *case.approvals, *case.extra_turns, "/exit"]
    stdin_text = "\n".join(turns) + "\n"

    swarmkit = _bin_dir() / "swarmkit"
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [str(swarmkit), "author", case.mode, str(target)],
            input=stdin_text,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
            env={**os.environ},
        )
        exit_code = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = exc.stdout.decode(errors="replace") if exc.stdout else ""
        stderr = (exc.stderr.decode(errors="replace") if exc.stderr else "") + "\n[TIMEOUT]"

    wall = time.monotonic() - started
    created = sorted(set(_list_files(target)) - before)

    # Validate the result (same path the CLI uses).
    validate = subprocess.run(
        [str(swarmkit), "validate", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    validates = validate.returncode == 0 or "no errors" in validate.stdout

    (out_dir / "transcript.log").write_text(stdout + "\n---STDERR---\n" + stderr, encoding="utf-8")
    (out_dir / "validate.log").write_text(validate.stdout + validate.stderr, encoding="utf-8")

    passed = len(created) >= 1 and validates

    meta = {
        "mode": case.mode,
        "id": case.id,
        "exit_code": exit_code,
        "wall_seconds": round(wall, 1),
        "turns_scripted": len(turns),
        "files_created": created,
        "validates": validates,
        "passed": passed,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def _report(results: list[dict[str, object]], report_path: Path) -> None:
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    lines = [
        "# swarmkit author bench — multi-turn validation",
        "",
        "Each case is a scripted conversation: requirement → approval turns → /exit.",
        "The REPL threads a stable `thread_id` so the LangGraph checkpointer resumes",
        "the author's state across turns — the same mechanism serve chat uses.",
        "",
        f"- cases: **{total}**",
        f"- passed (files landed + workspace validates): **{passed}**",
        "",
        "## Per case",
        "",
        "| mode | id | pass | exit | files | wall s | validate |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for r in results:
        mark = "✅" if r["passed"] else "❌"
        validate_cell = "yes" if r["validates"] else "no"
        files = len(r["files_created"])  # type: ignore[arg-type]
        lines.append(
            f"| {r['mode']} | {r['id']} | {mark} | {r['exit_code']} | "
            f"{files} | {r['wall_seconds']} | {validate_cell} |"
        )
    lines.append("")
    # Files per case for a quick eye-check.
    lines.append("## Files created, per case")
    lines.append("")
    for r in results:
        files = r["files_created"]  # type: ignore[assignment]
        if files:
            lines.append(f"- `{r['mode']}/{r['id']}`:")
            for p in files:  # type: ignore[attr-defined]
                lines.append(f"  - `{p}`")
        else:
            lines.append(f"- `{r['mode']}/{r['id']}`: (none)")
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="Comma-separated mode filter")
    parser.add_argument("--timeout", type=int, default=600, help="Per-case wall-time cap, seconds")
    args = parser.parse_args()

    only = set(args.only.split(",")) if args.only else None
    if RESULTS.exists():
        shutil.rmtree(RESULTS)

    results: list[dict[str, object]] = []
    for case in CASES:
        if only and case.mode not in only:
            continue
        out = RESULTS / f"{case.mode}-{case.id}"
        print(f"[{case.mode}/{case.id}] running…", flush=True)
        meta = _run_case(case, out, args.timeout)
        tag = "PASS" if meta["passed"] else "FAIL"
        print(
            f"  {tag} (exit {meta['exit_code']}, {meta['wall_seconds']}s, "
            f"{len(meta['files_created'])} files, validates={meta['validates']})",  # type: ignore[arg-type]
            flush=True,
        )
        results.append(meta)

    _report(results, RESULTS / "report.md")
    print(f"\nreport: {RESULTS / 'report.md'}", flush=True)
    # Non-zero exit when any case failed, so a CI job can gate on this.
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
