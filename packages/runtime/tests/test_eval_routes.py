"""Eval sets and eval runs on serve (design/details/eval-on-serve.md §5, PR 3 test plan).

A real `create_app` over a copy of the hello-swarm workspace, with the runtime's `run` replaced by
a scripted one after startup — so what is asserted is the HTTP contract and the row the service
leaves, not what a mock model happens to say. The script is thread-safe: TestClient serves the app
on its own loop in another thread.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from swarmkit_runtime.eval import EvalService
from swarmkit_runtime.persistence import SqliteStore
from swarmkit_runtime.persistence._tables import eval_runs
from swarmkit_runtime.server import create_app
from swarmkit_runtime.server._helpers import _required_action

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_WS = REPO_ROOT / "examples" / "hello-swarm" / "workspace"
SRC = REPO_ROOT / "packages" / "runtime" / "src" / "swarmkit_runtime"

_SET = """\
apiVersion: swarmkit/v1
kind: EvalSet
metadata: {id: greet, description: Greets people.}
target: hello
cases:
  - {id: one, input: "say one", expect: {contains: [one]}}
  - {id: two, input: "say two", expect: {contains: [two]}}
  - {id: three, input: "say three", expect: {contains: [three]}}
"""

TERMINAL = {"succeeded", "failed", "stopped", "error", "interrupted"}


class _Script:
    """Stands in for `WorkspaceRuntime.run`: echoes the input (so `contains` passes), unless the
    input names a failing case; holds on `block_on` until released."""

    def __init__(self) -> None:
        self.ran: list[str] = []
        self.fail: set[str] = set()
        self.block_on: str | None = None
        self.entered = threading.Event()
        self.release = threading.Event()

    async def run(self, topology_name: str, user_input: str, **_: Any) -> Any:
        self.ran.append(user_input)
        if self.block_on and self.block_on in user_input:
            self.entered.set()
            while not self.release.is_set():
                await asyncio.sleep(0.01)
        output = "nothing" if any(f in user_input for f in self.fail) else user_input
        return SimpleNamespace(output=output, events=[], usage=SimpleNamespace(cost_usd=0.01))


@pytest.fixture(autouse=True)
def _force_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWARMKIT_PROVIDER", "mock")


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    shutil.copytree(EXAMPLE_WS, root, ignore=shutil.ignore_patterns(".swarmkit", "__pycache__"))
    shutil.rmtree(root / "evals")
    (root / "evals").mkdir()
    (root / "evals" / "greet.yaml").write_text(_SET, encoding="utf-8")
    return root


def _client(ws: Path, script: _Script, **kw: Any) -> Iterator[TestClient]:
    app = create_app(ws, **kw)
    with TestClient(app) as c:
        app.state.runtime.run = script.run  # after startup: the lifespan built the runtime
        yield c


@pytest.fixture()
def script() -> _Script:
    return _Script()


@pytest.fixture()
def client(ws: Path, script: _Script) -> Iterator[TestClient]:
    yield from _client(ws, script)


def _wait(client: TestClient, run_id: str, *, until: set[str] = TERMINAL) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        row: dict[str, Any] = client.get(f"/evals/runs/{run_id}").json()
        if row["status"] in until:
            return row
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} never reached {until}")


def _start(client: TestClient, set_id: str = "greet") -> str:
    resp = client.post(f"/evals/{set_id}/run")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "running" and body["eval_set_id"] == set_id
    run_id: str = body["eval_run_id"]
    return run_id


def _free_slots(client: TestClient) -> int:
    sem: asyncio.Semaphore = client.app.state.job_semaphore  # type: ignore[attr-defined]
    return sem._value


# ---- eval sets: /api/evals ---------------------------------------------------------------------


def test_sets_are_listed_read_and_their_yaml_returned(client: TestClient) -> None:
    [listed] = client.get("/api/evals").json()
    assert listed == {
        "id": "greet",
        "name": "greet",
        "description": "Greets people.",
        "target": "hello",
        "case_count": 3,
        "last_run": None,
    }
    parsed = client.get("/api/evals/greet").json()
    assert [c["id"] for c in parsed["cases"]] == ["one", "two", "three"]
    assert client.get("/api/evals/greet/yaml").json()["yaml"] == _SET
    assert client.get("/api/evals/nope").status_code == 404


def test_a_set_is_created_replaced_and_deleted(client: TestClient, ws: Path) -> None:
    other = _SET.replace("id: greet,", "id: other,")
    created = client.post("/api/evals", json={"yaml": other})
    assert created.status_code == 200, created.text
    assert created.json() == {"valid": True, "id": "other", "case_count": 3}
    assert (ws / "evals" / "other.yaml").read_text() == other
    assert client.post("/api/evals", json={"yaml": other}).status_code == 409

    four = other + '  - {id: four, input: "say four"}\n'
    replaced = client.put("/api/evals/other", json={"yaml": four})
    assert replaced.json()["case_count"] == 4
    assert {s["id"]: s["case_count"] for s in client.get("/api/evals").json()} == {
        "greet": 3,
        "other": 4,
    }

    assert client.delete("/api/evals/other").json() == {"deleted": True, "id": "other"}
    assert not (ws / "evals" / "other.yaml").exists()
    assert client.delete("/api/evals/other").status_code == 404


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (_SET.replace("cases:", "unknown_key: 1\ncases:"), "schema"),
        (_SET.replace("target: hello", "target: missing"), "target.unknown"),
        (_SET.replace("id: greet,", "id: someone-else,"), "id.mismatch"),
        ("cases: [", "schema"),
    ],
)
def test_an_invalid_write_is_422_and_the_previous_file_stays(
    client: TestClient, ws: Path, body: str, code: str
) -> None:
    resp = client.put("/api/evals/greet", json={"yaml": body})
    assert resp.status_code == 422
    assert resp.json()["valid"] is False
    assert resp.json()["errors"][0]["code"] == code
    assert (ws / "evals" / "greet.yaml").read_text() == _SET


def test_a_new_set_that_fails_validation_leaves_no_file(client: TestClient, ws: Path) -> None:
    bad = _SET.replace("id: greet,", "id: fresh,").replace("target: hello", "target: missing")
    assert client.put("/api/evals/fresh", json={"yaml": bad}).status_code == 422
    assert client.post("/api/evals", json={"yaml": bad}).status_code == 422
    assert not (ws / "evals" / "fresh.yaml").exists()


def test_a_set_id_that_is_not_kebab_never_reaches_a_path(client: TestClient) -> None:
    """`load_eval_set` accepts a file path, for the CLI. A request must not reach it."""
    assert client.put("/api/evals/Not_Kebab", json={"yaml": _SET}).status_code == 422
    assert client.get("/api/evals/workspace.yaml").status_code == 404
    assert client.post("/evals/workspace.yaml/run").status_code == 404


def test_set_reads_are_the_read_tier_and_writes_admin() -> None:
    """Under `/api/*`, so the existing tier mapping applies — no new scope."""
    assert _required_action("GET", "/api/evals") == "read"
    assert _required_action("GET", "/api/evals/greet/yaml") == "read"
    for method in ("POST", "PUT", "DELETE"):
        assert _required_action(method, "/api/evals/greet") == "admin"
    assert _required_action("POST", "/evals/greet/run") == "run"
    assert _required_action("POST", "/evals/runs/x/stop") == "run"
    assert _required_action("POST", "/evals/runs/x/resume") == "run"
    assert _required_action("GET", "/evals/runs/x") == "read"


# ---- runs ----------------------------------------------------------------------------------------


def test_a_run_is_202_then_its_cases_arrive_then_it_finishes(
    client: TestClient, script: _Script
) -> None:
    script.block_on = "two"
    run_id = _start(client)
    assert script.entered.wait(5)

    partway = client.get(f"/evals/runs/{run_id}").json()
    assert partway["status"] == "running" and partway["recorded"] == 1
    assert [c["case_id"] for c in partway["report"]["cases"]] == ["one"]

    script.release.set()
    done = _wait(client, run_id)
    assert done["status"] == "succeeded" and done["source"] == "serve"
    assert (done["passed"], done["total"], done["recorded"]) == (3, 3, 3)
    assert done["cost_usd"] == pytest.approx(0.03)
    assert done["eval_set"]["metadata"]["id"] == "greet"

    [listed] = client.get("/evals/runs?set=greet").json()
    assert listed["id"] == run_id and "report" not in listed
    assert client.get("/evals/runs?set=other").json() == []
    [summary] = client.get("/api/evals").json()
    assert summary["last_run"]["id"] == run_id and summary["last_run"]["pass_rate"] == 1.0


def test_a_failing_case_finishes_failed(client: TestClient, script: _Script) -> None:
    script.fail = {"two"}
    assert _wait(client, _start(client))["status"] == "failed"


def test_unknown_set_and_run_are_404(client: TestClient) -> None:
    assert client.post("/evals/nope/run").status_code == 404
    assert client.get("/evals/runs/nope").status_code == 404
    assert client.post("/evals/runs/nope/stop").status_code == 404
    assert client.post("/evals/runs/nope/resume").status_code == 404


def test_a_set_whose_target_is_gone_is_422_and_writes_no_row(client: TestClient, ws: Path) -> None:
    (ws / "evals" / "greet.yaml").write_text(_SET.replace("target: hello", "target: missing"))
    assert client.post("/evals/greet/run").status_code == 422
    assert client.get("/evals/runs").json() == []


def test_the_api_tier_refuses_with_409(ws: Path, script: _Script) -> None:
    for c in _client(ws, script, enqueue_only=True):
        resp = c.post("/evals/greet/run")
        assert resp.status_code == 409 and "--role api" in resp.json()["detail"]
        assert c.get("/evals/runs").json() == []


def test_a_full_semaphore_is_429_and_writes_no_row(client: TestClient) -> None:
    sem: asyncio.Semaphore = client.app.state.job_semaphore  # type: ignore[attr-defined]
    taken = sem._value
    sem._value = 0
    try:
        assert client.post("/evals/greet/run").status_code == 429
    finally:
        sem._value = taken
    assert client.get("/evals/runs").json() == []


def test_stop_mid_run_then_resume_finishes_on_the_same_row(
    client: TestClient, script: _Script
) -> None:
    script.block_on = "two"
    run_id = _start(client)
    assert script.entered.wait(5)
    assert client.post(f"/evals/runs/{run_id}/stop").json() == {
        "eval_run_id": run_id,
        "stop_requested": True,
    }
    script.release.set()
    stopped = _wait(client, run_id)
    assert stopped["status"] == "stopped"
    assert [c["case_id"] for c in stopped["report"]["cases"]] == ["one", "two"]
    assert client.post(f"/evals/runs/{run_id}/stop").status_code == 409, "not running any more"

    script.block_on = None
    resumed = client.post(f"/evals/runs/{run_id}/resume")
    assert resumed.status_code == 202 and resumed.json()["eval_run_id"] == run_id
    done = _wait(client, run_id)
    assert done["status"] == "succeeded" and done["recorded"] == 3
    assert script.ran == ["say one", "say two", "say three"], "no case ran twice"
    assert client.post(f"/evals/runs/{run_id}/resume").status_code == 409, "it is finished"


# ---- capacity ------------------------------------------------------------------------------------


def test_an_eval_holds_one_slot_and_gives_it_back(client: TestClient, script: _Script) -> None:
    free = _free_slots(client)
    script.block_on = "two"
    run_id = _start(client)
    assert script.entered.wait(5)
    assert _free_slots(client) == free - 1, "one slot for the whole eval, not one per case"
    script.release.set()
    _wait(client, run_id)
    _settle(client, free)


@pytest.mark.parametrize("ending", ["failed", "stopped", "raised", "refused"])
def test_the_slot_comes_back_however_the_eval_ends(
    client: TestClient, script: _Script, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    free = _free_slots(client)
    if ending == "failed":
        script.fail = {"one"}
        _wait(client, _start(client))
    elif ending == "stopped":
        script.block_on = "one"
        run_id = _start(client)
        assert script.entered.wait(5)
        client.post(f"/evals/runs/{run_id}/stop")
        script.release.set()
        assert _wait(client, run_id)["status"] == "stopped"
    elif ending == "raised":

        async def _boom(self: Any, row: Any, **_: Any) -> Any:
            raise RuntimeError("the service broke")

        monkeypatch.setattr(EvalService, "execute", _boom)
        _start(client)
    else:  # admitted the slot, then the set turned out not to exist
        assert client.post("/evals/nope/run").status_code == 404
    _settle(client, free)


def _settle(client: TestClient, free: int) -> None:
    deadline = time.monotonic() + 5
    while _free_slots(client) != free and time.monotonic() < deadline:
        time.sleep(0.02)
    assert _free_slots(client) == free


# ---- restart and shutdown ------------------------------------------------------------------------


def test_serve_starting_over_a_stale_running_eval_marks_it_interrupted(
    ws: Path, script: _Script
) -> None:
    store = SqliteStore(ws)
    store.create_eval_run(
        "orphan", eval_set_id="greet", target="hello", source="serve", eval_set={}, total=3
    )
    store.create_eval_run(
        "live", eval_set_id="greet", target="hello", source="serve", eval_set={}, total=3
    )
    stale = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    with store.engine.begin() as conn:
        conn.execute(update(eval_runs).where(eval_runs.c.id == "orphan").values(heartbeat_at=stale))

    for c in _client(ws, script):
        assert c.get("/evals/runs/orphan").json()["status"] == "interrupted"
        assert c.get("/evals/runs/live").json()["status"] == "running", (
            "a fresh heartbeat is another instance's live eval"
        )


def test_shutting_down_mid_eval_leaves_it_interrupted_and_resumable(
    ws: Path, script: _Script
) -> None:
    """The lifespan cancels the eval *before* it closes the runtime the eval is using — not left to
    the loop's teardown, which would cancel it only after its runtime was gone."""
    script.block_on = "two"
    at_close: list[str] = []
    for c in _client(ws, script):
        run_id = _start(c)
        assert script.entered.wait(5)
        rt = c.app.state.runtime  # type: ignore[attr-defined]
        real_close = rt.close

        async def _close(run_id: str = run_id, real_close: Any = real_close) -> None:
            row = SqliteStore(ws).get_eval_run(run_id)
            at_close.append(row.status if row else "missing")
            await real_close()

        rt.close = _close
    assert at_close == ["interrupted"]
    row = SqliteStore(ws).get_eval_run(run_id)
    assert row is not None and row.status == "interrupted"
    assert [c["case_id"] for c in row.cases] == ["one"]


# ---- one service, both front doors --------------------------------------------------------------


def test_cli_and_http_rows_share_a_shape_and_each_diffs_against_the_other(
    client: TestClient, ws: Path, script: _Script
) -> None:
    """The CLI's `swarmkit eval` runs through the same `EvalService`; here directly, on the same
    store, with source `cli` — then serve's run diffs against it, and the CLI's next against
    serve's."""
    service = EvalService(client.app.state.runtime, SqliteStore(ws), ws)  # type: ignore[attr-defined]
    cli_row = asyncio.run(service.run("greet", source="cli"))

    script.fail = {"two"}
    http_row = _wait(client, _start(client))
    assert http_row["diff"]["prior_eval_run_id"] == cli_row.id
    assert http_row["diff"]["regressed"] == ["two"]

    [listed_http, listed_cli] = client.get("/evals/runs").json()
    assert listed_cli == cli_row.summary()
    assert set(listed_http) == set(listed_cli)
    assert (listed_http["source"], listed_cli["source"]) == ("serve", "cli")

    script.fail = set()
    again = asyncio.run(service.run("greet", source="cli"))
    assert again.diff is not None and again.diff["prior_eval_run_id"] == http_row["id"]
    assert again.diff["fixed"] == ["two"]


# ---- no agent can edit the cases it is measured against ------------------------------------------


def test_the_authoring_write_tool_refuses_evals(tmp_path: Path) -> None:
    """The authoring swarm's only disk write is `write_file`. An agent that could edit the cases
    it is scored against could pass any test (eval-on-serve.md §5)."""
    tool = SRC / "authoring_workspace" / "command_packs" / "author-tools" / "write_file.py"
    out = subprocess.run(
        [sys.executable, str(tool)],
        input=json.dumps({"files": {"evals/greet.yaml": _SET}}),
        env={**os.environ, "SWARMKIT_AUTHOR_TARGET_WORKSPACE": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 3
    assert json.loads(out.stdout)["error"] == "write_scope_violation"
    assert not (tmp_path / "evals").exists()


def test_only_the_human_tier_routes_write_eval_sets() -> None:
    """The eval-set writers are called from `/api/evals` (admin) and nowhere else — not an MCP
    door, not a skill, not a CLI an agent drives."""
    writers = ("put_eval_set(", "create_eval_set(", "delete_eval_set(")
    callers = sorted(
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if any(w in path.read_text(encoding="utf-8") for w in writers)
    )
    assert callers == ["server/_routes_evals.py", "server/_services.py"]
