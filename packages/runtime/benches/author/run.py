#!/usr/bin/env python3
"""Minimum-signal benchmark harness for `swarmkit author`.

For each prompt under `prompts/<mode>/*.yaml`:
  1. Spin up a tmp workspace, seed any `workspace_fixture.files`.
  2. Pipe the `requirement` + scripted `replies` into `swarmkit author <mode> <ws>`.
  3. Capture stdout/stderr, wall time, exit code.
  4. Run `swarmkit validate <ws>` and count files created under common dirs.
  5. Collect everything into results/<id>/ and emit results/report.md.

No LLM judge, no cost tracking yet — those are (2) in #1041. This harness answers
only: does the agent produce something, does it validate, how long.

Usage:
    uv run python packages/runtime/benches/author/run.py [--only MODE,MODE] [--filter ID]
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
from typing import Any

try:
    import yaml
except ImportError:
    sys.stderr.write("pyyaml missing; run inside the project venv (uv run python ...)\n")
    raise

ROOT = Path(__file__).resolve().parent
PROMPTS_DIR = ROOT / "prompts"
RESULTS_DIR = ROOT / "results"
TOPLEVEL_DIRS = ("topologies", "skills", "archetypes", "funnels", "triggers")


@dataclass
class PromptCase:
    path: Path
    mode: str
    id: str
    description: str
    requirement: str
    fixture_files: dict[str, str]
    expect: dict[str, Any]


@dataclass
class RunResult:
    case: PromptCase
    exit_code: int
    wall_seconds: float
    stdout: str
    stderr: str
    files_created: list[str]
    validates: bool
    validate_output: str

    @property
    def generated(self) -> bool:
        return self.exit_code == 0 and len(self.files_created) >= int(
            self.case.expect.get("files_created_min", 1)
        )

    @property
    def passed(self) -> bool:
        return self.generated and self.validates


def _load_cases() -> list[PromptCase]:
    cases: list[PromptCase] = []
    for mode_dir in sorted(PROMPTS_DIR.iterdir()):
        if not mode_dir.is_dir():
            continue
        for prompt_file in sorted(mode_dir.glob("*.yaml")):
            doc = yaml.safe_load(prompt_file.read_text(encoding="utf-8")) or {}
            fixture = (doc.get("workspace_fixture") or {}).get("files") or {}
            cases.append(
                PromptCase(
                    path=prompt_file,
                    mode=doc["mode"],
                    id=doc["id"],
                    description=doc.get("description", ""),
                    requirement=doc["requirement"].strip(),
                    fixture_files=fixture,
                    expect=doc.get("expect", {}),
                )
            )
    return cases


def _seed_workspace(workspace: Path, fixture_files: dict[str, str]) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    for rel, body in fixture_files.items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    # Minimum workspace needs a workspace.yaml.
    ws_file = workspace / "workspace.yaml"
    if not ws_file.exists():
        ws_file.write_text(
            "apiVersion: swarmkit/v1\nkind: Workspace\nmetadata:\n  id: bench\n  name: bench\n",
            encoding="utf-8",
        )


def _list_files(workspace: Path) -> list[str]:
    out: list[str] = []
    for sub in TOPLEVEL_DIRS:
        folder = workspace / sub
        if folder.is_dir():
            out.extend(sorted(str(p.relative_to(workspace)) for p in folder.glob("*")))
    return out


def _bin_dir() -> Path:
    return Path(sys.executable).parent


def _run_authoring(case: PromptCase, workspace: Path) -> tuple[int, float, str, str]:
    """Invoke the authoring agent as a one-shot `swarmkit run` against the bundled
    ``swarmkit:author:<mode>`` topology.

    Before #1045 PR 5 we had to pipe stdin to ``swarmkit author`` and script
    confirmations (``replies: [yes, yes, yes]``) — brittle because the agent would
    occasionally ask a clarifying question the harness couldn't answer and the whole
    run hung. The CLI + serve unification routes every entry point through
    ``WorkspaceRuntime.run``, so the harness now takes the same path a chat client
    takes: one request, one answer, one exit code.
    """
    swarmkit = _bin_dir() / "swarmkit"
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [
                str(swarmkit),
                "run",
                str(workspace),
                f"swarmkit:author:{case.mode}",
                "--input",
                case.requirement,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=int(case.expect.get("wall_time_max_s", 300)),
            env={**os.environ},
        )
        return proc.returncode, time.monotonic() - started, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        return (
            124,
            time.monotonic() - started,
            exc.stdout.decode(errors="replace") if exc.stdout else "",
            (exc.stderr.decode(errors="replace") if exc.stderr else "") + "\n[TIMEOUT]",
        )


def _validate(workspace: Path) -> tuple[bool, str]:
    swarmkit = _bin_dir() / "swarmkit"
    proc = subprocess.run(
        [str(swarmkit), "validate", str(workspace)],
        capture_output=True,
        text=True,
        check=False,
    )
    passed = proc.returncode == 0 or "no errors" in proc.stdout
    return passed, (proc.stdout + proc.stderr)


def _run_case(case: PromptCase, out_dir: Path) -> RunResult:
    workspace = out_dir / "workspace"
    if workspace.exists():
        shutil.rmtree(workspace)
    _seed_workspace(workspace, case.fixture_files)
    before = set(_list_files(workspace))

    exit_code, wall, stdout, stderr = _run_authoring(case, workspace)

    after = set(_list_files(workspace))
    created = sorted(after - before)
    validates, validate_output = _validate(workspace)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "transcript.log").write_text(stdout + "\n---STDERR---\n" + stderr, encoding="utf-8")
    (out_dir / "validate.log").write_text(validate_output, encoding="utf-8")
    (out_dir / "meta.json").write_text(
        json.dumps(
            {
                "mode": case.mode,
                "id": case.id,
                "exit_code": exit_code,
                "wall_seconds": round(wall, 2),
                "files_created": created,
                "validates": validates,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return RunResult(
        case=case,
        exit_code=exit_code,
        wall_seconds=wall,
        stdout=stdout,
        stderr=stderr,
        files_created=created,
        validates=validates,
        validate_output=validate_output,
    )


def _report(results: list[RunResult], report_path: Path) -> None:  # noqa: PLR0915
    lines = ["# swarmkit author bench — minimum-signal results", ""]
    totals = {"generated": 0, "validates": 0, "passed": 0}
    by_mode: dict[str, dict[str, int]] = {}
    for r in results:
        totals["generated"] += int(r.generated)
        totals["validates"] += int(r.validates)
        totals["passed"] += int(r.passed)
        bucket = by_mode.setdefault(r.case.mode, {"n": 0, "passed": 0})
        bucket["n"] += 1
        bucket["passed"] += int(r.passed)

    lines.append("## Headline")
    lines.append("")
    lines.append(f"- cases: **{len(results)}**")
    lines.append(f"- generated (exit 0 + >= min files): **{totals['generated']}**")
    lines.append(f"- validates: **{totals['validates']}**")
    lines.append(f"- passed (both): **{totals['passed']}**")
    lines.append("")

    lines.append("## By mode")
    lines.append("")
    lines.append("| mode | cases | passed |")
    lines.append("|---|---:|---:|")
    for mode in sorted(by_mode):
        m = by_mode[mode]
        lines.append(f"| {mode} | {m['n']} | {m['passed']} |")
    lines.append("")

    lines.append("## Per-prompt")
    lines.append("")
    lines.append("| mode | id | pass | exit | files | wall s | validate |")
    lines.append("|---|---|---|---:|---:|---:|---|")
    for r in results:
        pass_cell = "✅" if r.passed else "❌"
        validate_cell = "yes" if r.validates else "no"
        lines.append(
            f"| {r.case.mode} | {r.case.id} | {pass_cell} | {r.exit_code} | "
            f"{len(r.files_created)} | {r.wall_seconds:.1f} | {validate_cell} |"
        )
    lines.append("")

    lines.append("## Failure digest")
    lines.append("")
    had_failure = False
    for r in results:
        if r.passed:
            continue
        had_failure = True
        lines.append(f"### {r.case.mode}/{r.case.id}")
        reason = []
        if r.exit_code != 0:
            reason.append(f"exit {r.exit_code}")
        if not r.files_created:
            reason.append("no files created")
        if not r.validates:
            reason.append("validate failed")
        lines.append(f"- reason: {', '.join(reason) or 'generated but did not pass expectations'}")
        tail = (r.stderr or r.stdout).strip().splitlines()[-5:]
        if tail:
            lines.append("- transcript tail:")
            lines.append("")
            for line in tail:
                lines.append(f"      {line}")
            lines.append("")
    if not had_failure:
        lines.append("No failures.")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--only",
        default="",
        help="Comma-separated modes to include (default: all).",
    )
    p.add_argument(
        "--filter",
        default="",
        help="Only run prompts whose id contains this substring.",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    cases = _load_cases()
    if args.only:
        wanted = {m.strip() for m in args.only.split(",") if m.strip()}
        cases = [c for c in cases if c.mode in wanted]
    if args.filter:
        cases = [c for c in cases if args.filter in c.id]

    if not cases:
        sys.stderr.write("no prompts matched\n")
        return 2

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results: list[RunResult] = []
    for case in cases:
        sys.stderr.write(f"[{case.mode}/{case.id}] running… ")
        sys.stderr.flush()
        out_dir = RESULTS_DIR / case.id
        result = _run_case(case, out_dir)
        results.append(result)
        sys.stderr.write(
            f"{'OK' if result.passed else 'FAIL'} (exit {result.exit_code}, "
            f"{result.wall_seconds:.1f}s, {len(result.files_created)} files, "
            f"validates={result.validates})\n"
        )

    _report(results, RESULTS_DIR / "report.md")
    sys.stderr.write(f"\nreport: {RESULTS_DIR / 'report.md'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
