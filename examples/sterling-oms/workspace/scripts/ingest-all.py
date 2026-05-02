"""Run all Sterling knowledge ingestions in parallel.

Checks which env vars are set, runs applicable ingestions concurrently,
shows live progress, and reports a summary when done.

Usage:
    source .env
    python scripts/ingest-all.py              # run all applicable
    python scripts/ingest-all.py --reset       # delete indexes first, re-ingest
    python scripts/ingest-all.py --only cdt    # run just one ingestion
    python scripts/ingest-all.py --list        # show what would run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
WORKSPACE_DIR = SCRIPTS_DIR.parent


@dataclass
class Ingestion:
    name: str
    description: str
    required_env: list[str]
    command: list[str]
    reset_paths: list[str] = field(default_factory=list)
    optional: bool = False

    def is_ready(self) -> bool:
        return all(os.environ.get(v) for v in self.required_env)

    def missing_env(self) -> list[str]:
        return [v for v in self.required_env if not os.environ.get(v)]


def _resolve(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def build_ingestions() -> list[Ingestion]:
    return [
        Ingestion(
            name="cdt",
            description="CDT config dump → structured JSON",
            required_env=["STERLING_CDT_DIR", "STERLING_CDT_INDEX"],
            command=[
                sys.executable,
                str(SCRIPTS_DIR / "ingest-cdt.py"),
                _resolve("$STERLING_CDT_DIR"),
                "--output",
                _resolve("$STERLING_CDT_INDEX"),
            ],
            reset_paths=[_resolve("$STERLING_CDT_INDEX")],
        ),
        Ingestion(
            name="product-docs",
            description="Product docs → ChromaDB + FTS5",
            required_env=["STERLING_PRODUCT_DOCS_DIR"],
            command=[
                "uv",
                "run",
                str(SCRIPTS_DIR / "ingest-docs.py"),
            ],
            reset_paths=[
                _resolve("$STERLING_PRODUCT_DOCS_DIR/chromadb"),
                _resolve("$STERLING_PRODUCT_DOCS_DIR/fts.db"),
            ],
        ),
        Ingestion(
            name="project-docs",
            description="Project docs → ChromaDB + FTS5",
            required_env=["STERLING_PROJECT_DOCS_DIR"],
            command=[
                "uv",
                "run",
                str(SCRIPTS_DIR / "ingest-docs.py"),
            ],
            reset_paths=[
                _resolve("$STERLING_PROJECT_DOCS_DIR/chromadb"),
                _resolve("$STERLING_PROJECT_DOCS_DIR/fts.db"),
            ],
        ),
        Ingestion(
            name="reference-designs",
            description="Reference designs → ChromaDB + FTS5",
            required_env=["REFERENCE_DESIGNS_DIR"],
            command=[
                "uv",
                "run",
                str(SCRIPTS_DIR / "ingest-docs.py"),
            ],
            reset_paths=[
                _resolve("$REFERENCE_DESIGNS_DIR/chromadb"),
                _resolve("$REFERENCE_DESIGNS_DIR/fts.db"),
            ],
        ),
        Ingestion(
            name="api-summaries",
            description="API javadoc summaries → markdown for RAG",
            required_env=["STERLING_JAVADOCS_DIR", "STERLING_PRODUCT_DOCS_DIR"],
            command=[
                "uv",
                "run",
                str(WORKSPACE_DIR / "sterling_javadocs_server.py"),
                "--export-summaries",
                _resolve("$STERLING_PRODUCT_DOCS_DIR/api-reference"),
            ],
        ),
        Ingestion(
            name="code-graph",
            description="Graphify code knowledge graph",
            required_env=["STERLING_PROJECT_CODE_DIR"],
            command=[
                "uvx",
                "--from",
                "graphifyy",
                "graphify",
                "update",
                _resolve("$STERLING_PROJECT_CODE_DIR"),
            ],
            optional=True,
        ),
    ]


def env_for_ingestion(ing: Ingestion) -> dict[str, str]:
    """Build the subprocess environment with the right STERLING_DOCS_DIR."""
    env = dict(os.environ)
    docs_dir_map = {
        "product-docs": "STERLING_PRODUCT_DOCS_DIR",
        "project-docs": "STERLING_PROJECT_DOCS_DIR",
        "reference-designs": "REFERENCE_DESIGNS_DIR",
    }
    if ing.name in docs_dir_map:
        env["STERLING_DOCS_DIR"] = os.environ[docs_dir_map[ing.name]]
    return env


@dataclass
class Result:
    name: str
    status: str  # "ok", "failed", "skipped"
    elapsed: float = 0.0
    output: str = ""


async def run_ingestion(
    ing: Ingestion,
    reset: bool,
    results: dict[str, Result],
) -> None:
    t0 = time.time()

    if reset:
        import shutil

        for p in ing.reset_paths:
            path = Path(p)
            if path.is_dir():
                shutil.rmtree(path)
                print(f"  [{ing.name}] reset: removed {path}")
            elif path.is_file():
                path.unlink()
                print(f"  [{ing.name}] reset: removed {path}")

    cmd = ing.command
    if reset and ing.name in ("product-docs", "project-docs", "reference-designs"):
        cmd = [*cmd, "--reset"]

    env = env_for_ingestion(ing)
    print(f"  [{ing.name}] starting: {ing.description}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(WORKSPACE_DIR),
            env=env,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace")
        elapsed = time.time() - t0

        if proc.returncode == 0:
            last_lines = "\n".join(output.strip().splitlines()[-5:])
            print(f"  [{ing.name}] done in {elapsed:.1f}s")
            print(f"    {last_lines}")
            results[ing.name] = Result(ing.name, "ok", elapsed, output)
        else:
            last_lines = "\n".join(output.strip().splitlines()[-10:])
            print(f"  [{ing.name}] FAILED (exit {proc.returncode}) in {elapsed:.1f}s")
            print(f"    {last_lines}")
            results[ing.name] = Result(ing.name, "failed", elapsed, output)

    except FileNotFoundError as e:
        elapsed = time.time() - t0
        print(f"  [{ing.name}] FAILED: {e}")
        results[ing.name] = Result(ing.name, "failed", elapsed, str(e))


async def run_all(
    ingestions: list[Ingestion],
    reset: bool,
) -> dict[str, Result]:
    results: dict[str, Result] = {}
    tasks = [run_ingestion(ing, reset, results) for ing in ingestions]
    await asyncio.gather(*tasks)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run all Sterling knowledge ingestions in parallel",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing indexes before re-ingesting",
    )
    parser.add_argument(
        "--only",
        type=str,
        help="Run only this ingestion (by name)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Show what would run, without running",
    )
    args = parser.parse_args()

    all_ingestions = build_ingestions()

    if args.only:
        matches = [i for i in all_ingestions if i.name == args.only]
        if not matches:
            names = ", ".join(i.name for i in all_ingestions)
            print(f"Unknown ingestion: {args.only}")
            print(f"Available: {names}")
            sys.exit(1)
        all_ingestions = matches

    ready = [i for i in all_ingestions if i.is_ready() and not i.optional]
    ready_optional = [i for i in all_ingestions if i.is_ready() and i.optional]
    skipped = [i for i in all_ingestions if not i.is_ready()]

    if args.list:
        print("Ingestions that would run:")
        for i in ready:
            print(f"  {i.name:20s} {i.description}")
        if ready_optional:
            print("\nOptional (would run with --only):")
            for i in ready_optional:
                print(f"  {i.name:20s} {i.description}")
        if skipped:
            print("\nSkipped (missing env vars):")
            for i in skipped:
                missing = ", ".join(i.missing_env())
                print(f"  {i.name:20s} needs: {missing}")
        return

    if args.only:
        to_run = [i for i in all_ingestions if i.is_ready()]
    else:
        to_run = ready

    if not to_run:
        print("Nothing to ingest. Set environment variables first:")
        for i in all_ingestions:
            missing = ", ".join(i.missing_env())
            if missing:
                print(f"  {i.name:20s} needs: {missing}")
        print("\nRun: source .env")
        sys.exit(1)

    print(f"Running {len(to_run)} ingestion(s){' with --reset' if args.reset else ''}:\n")
    t0 = time.time()
    results = asyncio.run(run_all(to_run, args.reset))
    total = time.time() - t0

    print(f"\n{'='*60}")
    print(f"Completed in {total:.1f}s\n")

    ok = [r for r in results.values() if r.status == "ok"]
    failed = [r for r in results.values() if r.status == "failed"]

    if ok:
        print(f"  Succeeded ({len(ok)}):")
        for r in ok:
            print(f"    {r.name:20s} {r.elapsed:.1f}s")
    if failed:
        print(f"\n  Failed ({len(failed)}):")
        for r in failed:
            print(f"    {r.name:20s} {r.elapsed:.1f}s")
    if skipped:
        print(f"\n  Skipped ({len(skipped)}):")
        for i in skipped:
            missing = ", ".join(i.missing_env())
            print(f"    {i.name:20s} needs: {missing}")

    if failed:
        print(f"\n{len(failed)} ingestion(s) failed.")
        sys.exit(1)

    print("\nAll ingestions completed successfully.")


if __name__ == "__main__":
    main()
