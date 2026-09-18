"""`swarmkit skill` — find, add, import, check and remove (design/details/skill-registry.md).

Everything runs against a fixture catalogue directory (`SWARMKIT_SKILLS_CATALOGUE`), the way a
checkout or a mirror would be used; nothing here reaches the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from swarmkit_runtime.skills._registry import (
    SkillRegistryError,
    apply_add,
    check_skills,
    convert_skill_md,
    load_catalogue,
    plan_add,
    remove_skill,
    workspace_skills,
)
from typer.testing import CliRunner

REPO = Path(__file__).resolve().parents[3]
CATALOGUE = Path(__file__).resolve().parent / "fixtures" / "skill-catalogue"
EXAMPLE_WS = REPO / "examples" / "hello-swarm" / "workspace"

_WS = """\
apiVersion: swarmkit/v1
kind: Workspace
metadata: {id: w, name: W}
governance: {provider: mock}
memory: {enabled: false}
# the one server this workspace already has
mcp_servers:
  - id: fs
    transport: stdio
    command: ["python3", "-c", "pass"]   # keep this comment
    permission: readonly
"""


@pytest.fixture
def catalogue(monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("SWARMKIT_SKILLS_CATALOGUE", str(CATALOGUE))
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")
    return CATALOGUE


@pytest.fixture
def ws(tmp_path: Path, catalogue: Path) -> Path:
    (tmp_path / "workspace.yaml").write_text(_WS)
    (tmp_path / "skills").mkdir()
    return tmp_path


# ---- catalogue --------------------------------------------------------------------------------


def test_catalogue_from_a_directory_indexes_bundles_and_skills(catalogue: Path) -> None:
    cat = load_catalogue()
    assert set(cat.bundles) == {"git", "future"}
    assert cat.bundles["git"].skill_ids == ("git-log", "git-diff")
    assert cat.skills["git-log"].server_id == "git"
    assert cat.bundles["git"].verification["state"] == "verified"


def test_search_ranks_id_hits_above_description_hits(catalogue: Path) -> None:
    cat = load_catalogue()
    assert [s.id for s in cat.search("git")] == ["git-diff", "git-log"]
    assert [s.id for s in cat.search("commit history")] == ["git-log"]
    assert cat.search("nothing-like-this") == []
    assert cat.suggest("git-lg")[0] == "git-log"


def test_a_remote_catalogue_is_cached_for_a_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx  # noqa: PLC0415

    monkeypatch.setenv("SWARMKIT_SKILLS_CATALOGUE", "https://mirror.example.com/catalogue")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        rel = request.url.path.split("/catalogue/", 1)[1]
        if rel == "index.json":
            return httpx.Response(200, json=["git"])
        p = CATALOGUE / rel
        return httpx.Response(200, text=p.read_text()) if p.exists() else httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cache = tmp_path / "cache.json"
    cat = load_catalogue(client=client, cache=cache)
    assert "git-log" in cat.skills and cache.exists()
    n = len(calls)
    again = load_catalogue(client=client, cache=cache)  # served from the cache
    assert len(calls) == n and "git-log" in again.skills
    load_catalogue(client=client, cache=cache, refresh=True)
    assert len(calls) > n


# ---- add ------------------------------------------------------------------------------------


def test_plan_add_a_skill_yields_the_file_and_its_server(ws: Path) -> None:
    plan = plan_add(ws, "git-log")
    assert set(plan.skill_files) == {ws / "skills" / "git-log.yaml"}
    assert plan.server_entry is not None and plan.server_entry["id"] == "git"
    assert plan.server_state == "new"
    assert any("verified" in n for n in plan.notes)
    # planning wrote nothing
    assert list((ws / "skills").iterdir()) == []
    assert "git" not in (ws / "workspace.yaml").read_text()


def test_apply_add_writes_the_file_and_upserts_the_server_keeping_comments(ws: Path) -> None:
    result = apply_add(ws, plan_add(ws, "git-log"))
    assert result == {"skill_files": ["skills/git-log.yaml"], "server": True}
    text = (ws / "workspace.yaml").read_text()
    assert "# keep this comment" in text and "# the one server" in text
    assert "id: git" in text and "mcp-server-git" in text
    skill = yaml.safe_load((ws / "skills" / "git-log.yaml").read_text())
    assert skill["implementation"] == {"type": "mcp_tool", "server": "git", "tool": "git_log"}
    # idempotent: nothing to write the second time
    plan = plan_add(ws, "git-log")
    assert plan.server_state == "same"
    assert apply_add(ws, plan) == {"skill_files": [], "server": False}
    # and the workspace resolves with the new skill
    assert {s.id for s in workspace_skills(ws)} == {"git-log"}


def test_add_a_bundle_writes_every_skill_and_one_server(ws: Path) -> None:
    plan = plan_add(ws, "git")
    assert {p.name for p in plan.skill_files} == {"git-log.yaml", "git-diff.yaml"}
    apply_add(ws, plan)
    assert (ws / "workspace.yaml").read_text().count("id: git\n") == 1


def test_a_changed_server_entry_is_reported_as_differs(ws: Path) -> None:
    apply_add(ws, plan_add(ws, "git-log"))
    text = (
        (ws / "workspace.yaml")
        .read_text()
        .replace("permission: readonly\n    effects", "permission: elevated\n    effects")
    )
    (ws / "workspace.yaml").write_text(text)
    assert plan_add(ws, "git-log").server_state == "differs"


def test_add_refuses_a_floor_above_this_runtime(ws: Path) -> None:
    with pytest.raises(SkillRegistryError, match=r"needs runtime >=99\.0\.0"):
        plan_add(ws, "future")
    with pytest.raises(SkillRegistryError, match="did you mean git-log"):
        plan_add(ws, "git-lg")


def test_add_a_local_skill_file(ws: Path, tmp_path: Path) -> None:
    f = tmp_path / "mine.yaml"
    f.write_text((CATALOGUE / "skills/git/skills/git-diff.yaml").read_text())
    plan = plan_add(ws, str(f))
    assert plan.server_entry is None and set(plan.skill_files) == {ws / "skills" / "git-diff.yaml"}
    apply_add(ws, plan)
    assert (ws / "skills" / "git-diff.yaml").exists()


def test_add_rolls_back_a_server_entry_the_workspace_cannot_load(ws: Path) -> None:
    from swarmkit_runtime.skills._registry import AddPlan  # noqa: PLC0415

    before = (ws / "workspace.yaml").read_text()
    bad = AddPlan(
        skill_files={},
        server_entry={"id": "broken", "transport": "carrier-pigeon"},  # not a transport
        server_state="new",
        notes=(),
    )
    with pytest.raises(SkillRegistryError, match="mcp_servers/broken"):
        apply_add(ws, bad)
    assert (ws / "workspace.yaml").read_text() == before


# ---- import -----------------------------------------------------------------------------------

_SKILL_MD = """\
---
name: brand-voice
description: Write in the company voice.
metadata:
  version: "2.1"
---
# Brand voice

Short sentences. No exclamation marks. Say the price.
"""


def test_convert_skill_md_is_an_llm_prompt_skill() -> None:
    raw = convert_skill_md(_SKILL_MD, "anthropics/skills/brand-voice/SKILL.md")
    assert raw["metadata"] == {
        "id": "brand-voice",
        "name": "Brand Voice",
        "description": "Write in the company voice.",
    }
    assert raw["implementation"]["type"] == "llm_prompt"
    assert raw["implementation"]["prompt"].startswith("# Brand voice\n\nShort sentences.")
    assert raw["provenance"] == {
        "authored_by": "imported_from_registry",
        "version": "2.1.0",
        "registry": "anthropics/skills/brand-voice/SKILL.md",
    }


def test_convert_refuses_a_file_without_frontmatter_or_name() -> None:
    with pytest.raises(SkillRegistryError, match="no YAML frontmatter"):
        convert_skill_md("# just markdown\n", "x")
    with pytest.raises(SkillRegistryError, match="no `name`"):
        convert_skill_md("---\ndescription: d\n---\nbody\n", "x")


# ---- check ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_reports_ok_missing_and_failed(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from swarmkit_runtime import _workspace_runtime as wr  # noqa: PLC0415

    apply_add(ws, plan_add(ws, "git"))

    class _Manager:
        async def list_tools(self, server_id: str) -> list[dict[str, Any]]:
            if server_id == "git":
                return [{"name": "git_log"}]  # git_diff was renamed upstream
            raise RuntimeError("server refused to start")

        async def close_all(self) -> None:
            return None

    class _Runtime:
        def __init__(self, workspace: Any) -> None:
            self.workspace = workspace
            self.mcp_manager = _Manager()

    from swarmkit_runtime.resolver import resolve_workspace  # noqa: PLC0415

    resolved = resolve_workspace(ws)
    monkeypatch.setattr(
        wr.WorkspaceRuntime, "from_workspace_path", staticmethod(lambda p: _Runtime(resolved))
    )
    results = {r.skill_id: r for r in await check_skills(ws)}
    assert results["git-log"].status == "ok"
    assert results["git-diff"].status == "missing"
    assert "git_log" in results["git-diff"].detail


# ---- remove -----------------------------------------------------------------------------------


def test_remove_refuses_a_held_skill_and_deletes_an_unheld_one(ws: Path) -> None:
    apply_add(ws, plan_add(ws, "git"))
    (ws / "topologies").mkdir()
    (ws / "topologies" / "t.yaml").write_text(
        "apiVersion: swarmkit/v1\nkind: Topology\nmetadata: {name: t, version: 0.1.0}\n"
        "agents:\n  root:\n    id: a\n    role: root\n    model: {provider: mock, name: m}\n"
        "    prompt: {system: hi}\n    skills: [git-log]\n"
    )
    held = {s.id: s.held_by for s in workspace_skills(ws)}
    assert held["git-log"] == ("t/a",) and held["git-diff"] == ()
    with pytest.raises(SkillRegistryError, match="held by t/a"):
        remove_skill(ws, "git-log")
    assert remove_skill(ws, "git-diff") == ws / "skills" / "git-diff.yaml"
    assert not (ws / "skills" / "git-diff.yaml").exists()


# ---- the CLI ----------------------------------------------------------------------------------


def test_cli_end_to_end(ws: Path, tmp_path: Path) -> None:
    from swarmkit_runtime.cli import app  # noqa: PLC0415

    r = CliRunner()
    out = r.invoke(app, ["skill", "list", "--available", "-w", str(ws)])
    assert out.exit_code == 0 and "git-log" in out.stdout and "verified" in out.stdout
    out = r.invoke(app, ["skill", "search", "history", "-w", str(ws), "--json"])
    assert [h["id"] for h in json.loads(out.stdout)["catalogue"]] == ["git-log"]
    out = r.invoke(app, ["skill", "add", "git-log", "-w", str(ws), "--dry-run"])
    assert out.exit_code == 0 and "dry run" in out.stdout
    assert not (ws / "skills" / "git-log.yaml").exists()
    out = r.invoke(app, ["skill", "add", "git-log", "-w", str(ws)], input="n\n")
    assert "nothing written" in out.stdout and not (ws / "skills" / "git-log.yaml").exists()
    out = r.invoke(app, ["skill", "add", "git-log", "-w", str(ws), "--yes"])
    assert out.exit_code == 0 and "mcp_servers updated" in out.stdout
    out = r.invoke(app, ["skill", "list", "-w", str(ws)])
    assert "git-log" in out.stdout and "mcp_tool → git" in out.stdout
    out = r.invoke(app, ["skill", "show", "git-log", "-w", str(ws)])
    assert "tool: git_log" in out.stdout
    md = tmp_path / "SKILL.md"
    md.write_text(_SKILL_MD)
    out = r.invoke(app, ["skill", "import", str(md), "-w", str(ws)])
    assert out.exit_code == 0 and "brand-voice" in out.stdout
    out = r.invoke(app, ["skill", "remove", "brand-voice", "-w", str(ws)])
    assert out.exit_code == 0 and not (ws / "skills" / "brand-voice.yaml").exists()
    out = r.invoke(app, ["skill", "add", "no-such-skill", "-w", str(ws)])
    assert out.exit_code == 1 and "not in the catalogue" in out.stderr


# ---- HTTP: the portal's peer surface ---------------------------------------------------------


def test_http_routes_are_thin_over_the_service(ws: Path) -> None:
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from swarmkit_runtime.server import create_app  # noqa: PLC0415

    with TestClient(create_app(ws)) as client:
        cat = client.get("/api/skill-catalogue", params={"q": "history"}).json()
        assert [s["id"] for s in cat["skills"]] == ["git-log"]
        assert cat["skills"][0]["installed"] is False
        dry = client.post("/api/skills/add", json={"ref": "git-log", "dry_run": True}).json()
        assert dry["applied"] is None and "skills/git-log.yaml" in dry["skill_files"]
        assert not (ws / "skills" / "git-log.yaml").exists()
        done = client.post("/api/skills/add", json={"ref": "git-log"}).json()
        assert done["applied"] == {"skill_files": ["skills/git-log.yaml"], "server": True}
        # the running workspace picked it up without a restart
        assert client.get("/api/skill-catalogue").json()["skills"][1]["installed"] is True
        assert "git-log" in {s["id"] for s in client.get("/skills").json()}
        bad = client.post("/api/skills/add", json={"ref": "nope"})
        assert bad.status_code == 400 and "not in the catalogue" in bad.json()["detail"]
        imp = client.post(
            "/api/skills/import", json={"text": _SKILL_MD, "origin": "upload", "dry_run": True}
        ).json()
        assert imp["skill"]["metadata"]["id"] == "brand-voice" and imp["written"] is False
