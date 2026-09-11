"""Demo: attachments on a run (design/details/images-on-both-executors.md).

A caller that already holds a file — a snapshot poller, a webhook with an upload — passes it beside
the input instead of making an agent go and fetch it. One model call, no tool round-trip.

Shows, in order:

  1. a run with no attachments — the path every existing caller takes, unchanged;
  2. `--attach` / `attachments:` putting real bytes in the entry agent's first message;
  3. the attachment reaching the ENTRY agent and no downstream node (the scoping rule that got the
     M8 version of this reverted);
  4. the audit record: name, media type, size and digest — never the content;
  5. the four refusals, each with the reason: a path out of the workspace, a declared type, a URL
     source, and a type nothing can carry yet.

Run it:

    uv run python packages/runtime/demos/run_attachments.py
"""

from __future__ import annotations

import asyncio
import base64
import tempfile
from pathlib import Path
from typing import Any

from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.attachments import AttachmentError

# A 1x1 PNG, so the demo needs no fixture file and the bytes are genuinely an image.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

WORKSPACE = """\
apiVersion: swarmkit/v1
kind: Workspace
metadata:
  id: attach-demo
  name: Attachment demo
"""

# A root that delegates, so the "entry agent only" rule has something to be true ABOUT: the worker
# must not receive the image.
TOPOLOGY = """\
apiVersion: swarmkit/v1
kind: Topology
metadata:
  name: look
  version: 0.1.0
agents:
  root:
    id: root
    role: root
    model: {provider: mock, name: mock}
    prompt:
      system: You look at what you are given.
    children:
      - id: helper
        role: worker
        model: {provider: mock, name: mock}
        prompt:
          system: You help.
"""


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def build_workspace(root: Path) -> Path:
    (root / "topologies").mkdir(parents=True)
    (root / "workspace.yaml").write_text(WORKSPACE)
    (root / "topologies" / "look.yaml").write_text(TOPOLOGY)
    (root / "snapshots").mkdir()
    (root / "snapshots" / "gate.png").write_bytes(PNG)
    (root / "report.pdf").write_bytes(b"%PDF-1.7\n" + b"\x00" * 32)
    return root


def image_blocks(runtime: WorkspaceRuntime) -> list[Any]:
    provider: Any = runtime._provider_registry.get("mock")
    return [
        block
        for call in provider.calls
        for message in call.messages
        if not isinstance(message.content, str)
        for block in message.content
        if block.type == "image"
    ]


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = build_workspace(Path(tmp) / "workspace")

        rule("1. No attachments — unchanged for every existing caller")
        runtime = WorkspaceRuntime.from_workspace_path(ws)
        await runtime.run("look", "hello")
        print(f"image blocks sent to the model: {len(image_blocks(runtime))}  (expected 0)")

        rule("2. One image attached — the bytes reach the model")
        events: list[Any] = []
        runtime = WorkspaceRuntime.from_workspace_path(ws)
        audit: Any = runtime._audit_provider
        original = audit.record

        async def capture(event: Any) -> None:
            events.append(event)
            await original(event)

        audit.record = capture
        await runtime.run(
            "look", "what is at the gate?", attachments=[{"path": "snapshots/gate.png"}]
        )

        blocks = image_blocks(runtime)
        print(f"image blocks sent to the model: {len(blocks)}")
        print(f"  media type : {blocks[0].image_media_type}")
        print(f"  bytes match the file on disk: {base64.b64decode(blocks[0].image_data) == PNG}")

        rule("3. Entry agent only — asked of each agent directly")
        # Asked of the prompt builder rather than inferred from the run: a mock model does not
        # delegate, so a live run alone would prove nothing about the worker. This is the rule the
        # M8 version broke — it broadcast attachments through state, handing images to text-only
        # supervisors, and was reverted whole.
        from swarmkit_runtime.attachments import resolve_all  # noqa: PLC0415
        from swarmkit_runtime.langgraph_compiler._prompts import (  # noqa: PLC0415
            _build_prompt_messages,
        )

        topology = runtime.workspace.topologies["look"]
        root_agent = topology.root
        worker = root_agent.children[0]
        state: Any = {
            "input": "what is at the gate?",
            "messages": [],
            "agent_results": {},
            "delegation_counts": {},
            "task_plan": {},
            "current_agent": "",
            "output": "",
            "node_errors": {},
            "diffs": {},
            "attachments": resolve_all([{"path": "snapshots/gate.png"}], ws),
        }

        def images_for(agent: Any) -> int:
            return sum(
                1
                for m in _build_prompt_messages(agent, state)
                if not isinstance(m.content, str)
                for b in m.content
                if b.type == "image"
            )

        print(f"  root   (role={root_agent.role:<6}) receives : {images_for(root_agent)} image(s)")
        print(f"  helper (role={worker.role:<6}) receives : {images_for(worker)} image(s)")
        print("A node that wants a file it was not handed asks through a skill — that is what")
        print("skills are for, and it keeps the grant governed and audited like any other.")

        rule("4. Audited by description, never by content")
        record = next(e for e in events if e.event_type == "run.attachments").payload[
            "attachments"
        ][0]
        for key in ("name", "media_type", "size", "source", "sha256"):
            print(f"  {key:<11}: {record[key]}")
        print(f"  bytes in the audit record       : {'YES — bug' if 'data' in record else 'no'}")
        print(
            "The digest is what makes the reference checkable once the source file may have moved."
        )

        rule("5. Refusals, each with a reason a person can act on")
        for label, spec in [
            ("path out of the workspace", {"path": "../../etc/passwd"}),
            ("caller declaring the type", {"path": "snapshots/gate.png", "type": "image/png"}),
            ("url source", {"url": "https://example.com/x.png"}),
            ("a type nothing carries yet", {"path": "report.pdf"}),
        ]:
            try:
                await runtime.run("look", "hi", attachments=[spec])
                print(f"  {label:<28}: NOT REFUSED — bug")
            except AttachmentError as exc:
                print(f"  {label:<28}: {exc}")

        print("\nEach fails the CALL, not the run: nothing executed, nothing billed, no job row.")


if __name__ == "__main__":
    import os

    os.environ.setdefault("SWARMKIT_PROVIDER", "mock")
    os.environ.setdefault("SWARMKIT_MODEL", "mock")
    asyncio.run(main())
