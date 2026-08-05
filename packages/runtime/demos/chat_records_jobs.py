#!/usr/bin/env python
"""Demo: a chat conversation is recorded, and its audit trail points back at it.

    uv run python packages/runtime/demos/chat_records_jobs.py

Runs two turns of a real conversation against a mock model, then shows the job rows and the audit
run ids. Before this change the job count was 0 and every turn's audit rows carried a fresh random
UUID that no conversation referenced — the events existed and could not be found from the thing
that caused them.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from swarmkit_runtime._conversation import ConversationManager
from swarmkit_runtime._workspace_runtime import WorkspaceRuntime
from swarmkit_runtime.governance._mock import MockGovernanceProvider
from swarmkit_runtime.model_providers import MockModelProvider, ProviderRegistry
from swarmkit_runtime.persistence import storage_for_workspace
from swarmkit_runtime.resolver import resolve_workspace

REFERENCE = Path(__file__).resolve().parents[3] / "reference"
TOPOLOGY = "code-review"
MESSAGES = ["review this diff", "and the tests?"]


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        shutil.copytree(REFERENCE, ws)
        (ws / ".swarmkit").mkdir(exist_ok=True)

        registry = ProviderRegistry()
        registry.register(MockModelProvider())
        runtime = WorkspaceRuntime(
            workspace=resolve_workspace(ws),
            workspace_root=ws,
            provider_registry=registry,
            governance=MockGovernanceProvider(allow_all=True),
            mcp_manager=None,
        )

        manager = ConversationManager(runtime, ws)
        conversation = manager.create(TOPOLOGY)
        print(f"conversation {conversation.id} on {TOPOLOGY}\n")

        for message in MESSAGES:
            await manager.send(conversation, message)
            print(f"  sent: {message}")

        store = storage_for_workspace(ws).store()
        print("\njob rows:")
        for job in store.list_jobs():
            cost = job.usage_cost_usd or 0.0
            print(f"  {job.id:<28} {job.status:<10} conversation={job.correlation_id}  ${cost:.2f}")

        scoped = store.list_jobs(correlation_id=conversation.id)
        print(
            f"\njust this conversation "
            f"(GET /jobs/history?correlation_id={conversation.id}): {len(scoped)} turns"
        )

        events = [e async for e in runtime.audit_provider.query(limit=100)]
        run_ids = sorted({e.run_id or "" for e in events})
        print(f"\naudit rows: {len(events)}, under run ids {run_ids}")
        print("Each is the turn's id — so its audit trail and its trace are reachable from the")
        print("conversation, instead of sitting under a UUID nothing points at.")


if __name__ == "__main__":
    asyncio.run(main())
