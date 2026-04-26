"""Knowledge MCP Server — live SwarmKit corpus for any MCP client.

Exposes design docs, schemas, reference skills, and workspace state as
searchable MCP tools. The live counterpart to ``swarmkit knowledge-pack``.

See ``design/details/knowledge-mcp-server.md``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from mcp.server.fastmcp import FastMCP

from swarmkit_runtime.cli._knowledge import find_repo_root

server = FastMCP("swarmkit-knowledge")

_NOTES_EXCLUDE = {"README.md", "_template.md"}

_repo_root: Path | None = None


def _get_repo_root() -> Path:
    global _repo_root  # noqa: PLW0603
    if _repo_root is None:
        found = find_repo_root()
        if found is None:
            msg = "Cannot locate SwarmKit repo root. Run from inside the repo."
            raise RuntimeError(msg)
        _repo_root = found
    return _repo_root


def _set_repo_root(path: Path) -> None:
    global _repo_root  # noqa: PLW0603
    _repo_root = path.resolve()


# ---- corpus discovery (mirrors knowledge-pack) --------------------------


def _discover_design_notes(root: Path) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for f in sorted((root / "design" / "details").glob("*.md")):
        if f.name in _NOTES_EXCLUDE:
            continue
        text = f.read_text(encoding="utf-8")
        fm = _parse_frontmatter(text)
        notes.append(
            {
                "slug": f.stem,
                "title": fm.get("title", f.stem),
                "description": fm.get("description", ""),
                "tags": fm.get("tags", []),
                "status": fm.get("status", ""),
                "path": str(f.relative_to(root)),
            }
        )
    return notes


def _parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return {}


def _read_sections(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    sections: list[dict[str, str]] = []
    current_heading = path.name
    current_lines: list[str] = []

    for line in text.split("\n"):
        if line.startswith("## "):
            if current_lines:
                sections.append(
                    {"heading": current_heading, "content": "\n".join(current_lines).strip()}
                )
            current_heading = line.lstrip("# ").strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append({"heading": current_heading, "content": "\n".join(current_lines).strip()})
    return sections


# ---- search engine (keyword, term-frequency) ----------------------------


def _score_section(query_terms: list[str], text: str) -> float:
    text_lower = text.lower()
    score = 0.0
    for term in query_terms:
        score += text_lower.count(term)
    return score


def _build_corpus(root: Path) -> list[dict[str, Any]]:
    corpus: list[dict[str, Any]] = []

    md_patterns = [
        "design/details/*.md",
        "docs/notes/*.md",
        "README.md",
        "CLAUDE.md",
        "packages/*/CLAUDE.md",
    ]
    for pattern in md_patterns:
        for f in sorted(root.glob(pattern)):
            if f.name in _NOTES_EXCLUDE:
                continue
            for section in _read_sections(f):
                corpus.append(
                    {
                        "file": str(f.relative_to(root)),
                        "heading": section["heading"],
                        "content": section["content"],
                    }
                )

    for f in sorted((root / "packages" / "schema" / "schemas").glob("*.json")):
        content = f.read_text(encoding="utf-8")
        corpus.append(
            {
                "file": str(f.relative_to(root)),
                "heading": f.stem,
                "content": content,
            }
        )

    return corpus


# ---- MCP tools -----------------------------------------------------------


@server.tool()
def search_docs(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search SwarmKit design docs, schemas, and notes by keyword."""
    root = _get_repo_root()
    corpus = _build_corpus(root)
    terms = [t.lower() for t in re.split(r"\s+", query.strip()) if t]
    if not terms:
        return []

    scored = []
    for entry in corpus:
        text = f"{entry['heading']} {entry['content']}"
        score = _score_section(terms, text)
        if score > 0:
            snippet = entry["content"][:300]
            scored.append(
                (
                    score,
                    {
                        "file": entry["file"],
                        "heading": entry["heading"],
                        "snippet": snippet,
                        "score": str(round(score, 1)),
                    },
                )
            )

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:max_results]]


@server.tool()
def get_schema(
    artifact_type: str,
) -> dict[str, Any]:
    """Return the canonical JSON Schema for a SwarmKit artifact type.

    Valid types: topology, skill, archetype, workspace, trigger.
    """
    root = _get_repo_root()
    schema_path = root / "packages" / "schema" / "schemas" / f"{artifact_type}.schema.json"
    if not schema_path.is_file():
        valid = list_schemas()
        return {"error": f"Unknown artifact type '{artifact_type}'. Valid: {valid}"}
    result: dict[str, Any] = json.loads(schema_path.read_text(encoding="utf-8"))
    return result


@server.tool()
def list_schemas() -> list[str]:
    """List available JSON Schema artifact types."""
    root = _get_repo_root()
    schemas_dir = root / "packages" / "schema" / "schemas"
    return sorted(f.stem.replace(".schema", "") for f in schemas_dir.glob("*.schema.json"))


@server.tool()
def get_design_note(slug: str) -> dict[str, Any]:
    """Return a design note by slug (e.g. 'mcp-client', 'governance-provider-interface')."""
    root = _get_repo_root()
    path = root / "design" / "details" / f"{slug}.md"
    if not path.is_file():
        available = [n["slug"] for n in _discover_design_notes(root)]
        return {"error": f"Design note '{slug}' not found. Available: {available}"}
    text = path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    body_start = text.find("---", 3)
    body = text[body_start + 3 :].strip() if body_start != -1 else text
    return {"frontmatter": fm, "content": body}


@server.tool()
def list_design_notes(tag: str = "") -> list[dict[str, Any]]:
    """List all design notes with frontmatter. Optionally filter by tag."""
    root = _get_repo_root()
    notes = _discover_design_notes(root)
    if tag:
        notes = [n for n in notes if tag in n.get("tags", [])]
    return notes


@server.tool()
def list_reference_skills() -> list[dict[str, Any]]:
    """List reference skills under reference/skills/ with metadata."""
    root = _get_repo_root()
    skills_dir = root / "reference" / "skills"
    if not skills_dir.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for f in sorted(skills_dir.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        meta = data.get("metadata", {})
        impl = data.get("implementation", {})
        results.append(
            {
                "id": meta.get("id", f.stem),
                "name": meta.get("name", ""),
                "description": meta.get("description", ""),
                "category": data.get("category", ""),
                "server": impl.get("server", ""),
                "tool": impl.get("tool", ""),
            }
        )
    return results


@server.tool()
def validate_workspace(path: str) -> dict[str, Any]:
    """Resolve a workspace directory and return the resolved state or errors."""
    from swarmkit_runtime.errors import ResolutionErrors  # noqa: PLC0415
    from swarmkit_runtime.resolver import resolve_workspace  # noqa: PLC0415

    ws_path = Path(path).resolve()
    if not ws_path.is_dir():
        return {"error": f"Directory not found: {path}"}

    try:
        workspace = resolve_workspace(ws_path)
    except ResolutionErrors as exc:
        return {
            "valid": False,
            "errors": [
                {
                    "code": e.code,
                    "message": e.message,
                    "file": str(e.artifact_path),
                    "suggestion": e.suggestion,
                }
                for e in exc.errors
            ],
        }
    except FileNotFoundError as exc:
        return {"valid": False, "errors": [{"message": str(exc)}]}

    return {
        "valid": True,
        "workspace_id": str(workspace.raw.metadata.id),
        "topologies": sorted(workspace.topologies.keys()),
        "skills": sorted(workspace.skills.keys()),
        "archetypes": sorted(workspace.archetypes.keys()),
    }


@server.tool()
def get_error_reference(code: str) -> dict[str, str]:
    """Look up a validation error code and return description + fix suggestion."""
    root = _get_repo_root()
    loader_doc = root / "design" / "details" / "topology-loader.md"
    if not loader_doc.is_file():
        return {"code": code, "description": "Error reference not available.", "fix": ""}

    text = loader_doc.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"[`*]*{re.escape(code)}[`*]*\s*[\u2014\u2013\-]\s*(.+?)(?:\n\n|\n[#|])",
        re.DOTALL,
    )
    match = pattern.search(text)
    if match:
        desc = match.group(1).strip()
        return {
            "code": code,
            "description": desc,
            "fix": f"Search the topology-loader design note for '{code}'.",
        }
    return {
        "code": code,
        "description": f"Error code '{code}' not found in the reference.",
        "fix": (
            "Run `swarmkit validate <workspace>` for the full error "
            "with file pointer and suggestion."
        ),
    }


# ---- workspace write tools (authoring swarm) ----------------------------

_YAML_SUBDIRS = {"topologies", "skills", "archetypes", "triggers", "schedules"}
_TEST_SUBDIRS = {"tests"}
_ALLOWED_SUBDIRS = _YAML_SUBDIRS | _TEST_SUBDIRS


def _safe_workspace_path(workspace: str, file_path: str) -> Path | None:
    """Resolve a workspace-relative path and validate it's safe to write."""
    ws = Path(workspace).resolve()
    target = (ws / file_path).resolve()
    if not str(target).startswith(str(ws)):
        return None
    parts = Path(file_path).parts
    if not parts or parts[0] not in _ALLOWED_SUBDIRS:
        if file_path == "workspace.yaml":
            return target
        return None
    allowed_ext = {".py"} if parts[0] in _TEST_SUBDIRS else {".yaml", ".yml"}
    if target.suffix not in allowed_ext:
        return None
    return target


@server.tool()
def write_workspace_file(workspace: str, file_path: str, content: str) -> dict[str, str]:
    """Write a YAML artifact to a workspace directory.

    file_path must be workspace-relative and under an allowed subdirectory
    (topologies/, skills/, archetypes/, triggers/, schedules/) or be
    workspace.yaml itself. Only .yaml/.yml extensions are permitted.
    """
    target = _safe_workspace_path(workspace, file_path)
    if target is None:
        return {
            "error": (
                f"Refused to write '{file_path}'. Must be under "
                f"{sorted(_ALLOWED_SUBDIRS)} or be workspace.yaml, "
                f"with a .yaml/.yml extension."
            )
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"written": str(target), "size": str(len(content))}


@server.tool()
def read_workspace_file(workspace: str, file_path: str) -> dict[str, str]:
    """Read a YAML file from a workspace directory (for edit mode)."""
    ws = Path(workspace).resolve()
    target = (ws / file_path).resolve()
    if not str(target).startswith(str(ws)):
        return {"error": f"Path traversal rejected: {file_path}"}
    if not target.is_file():
        return {"error": f"File not found: {file_path}"}
    return {"path": file_path, "content": target.read_text(encoding="utf-8")}


# ---- test execution tool (authoring swarm quality gate) ------------------


@server.tool()
def run_pytest(
    workspace: str,
    test_file: str,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Run a pytest file against a workspace and return pass/fail + output.

    Restricted to files ending in ``_test.py`` or ``test_*.py`` under the
    workspace directory. Runs in a subprocess with a timeout.
    """
    import subprocess  # noqa: PLC0415

    ws = Path(workspace).resolve()
    target = (ws / test_file).resolve()

    if not str(target).startswith(str(ws)):
        return {"error": f"Path traversal rejected: {test_file}"}
    if not target.is_file():
        return {"error": f"Test file not found: {test_file}"}
    if not (target.name.startswith("test_") or target.name.endswith("_test.py")):
        return {"error": f"Not a test file (must be test_*.py or *_test.py): {target.name}"}

    try:
        result = subprocess.run(
            ["uv", "run", "pytest", str(target), "-x", "--tb=short", "-q"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=str(ws),
            check=False,
        )
        return {
            "passed": str(result.returncode == 0),
            "exit_code": str(result.returncode),
            "stdout": result.stdout[-2000:] if result.stdout else "",
            "stderr": result.stderr[-1000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"passed": "false", "error": f"Test timed out after {timeout_seconds}s"}
    except FileNotFoundError:
        return {"passed": "false", "error": "pytest not available (uv run pytest failed)"}


def run_server(repo_root: Path | None = None) -> None:
    """Entry point for the CLI launcher."""
    if repo_root is not None:
        _set_repo_root(repo_root)
    else:
        _get_repo_root()
    server.run()
