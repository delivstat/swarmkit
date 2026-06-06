#!/usr/bin/env python3
"""Workspace memory demo — run with: uv run python docs/examples/memory-demo.py

Demonstrates:
1. MemoryStore CRUD + TF-IDF search
2. Context injection (what the agent sees)
3. LLM insight extraction (mocked)
4. GBrain page format
5. Persistence across restarts
6. User deletion (GDPR)
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

from swarmkit_runtime.memory import MemoryEntry, MemoryStore
from swarmkit_runtime.memory._gbrain import _build_memory_page
from swarmkit_runtime.memory._hooks import extract_and_save, retrieve_context


def demo_crud_and_search() -> None:
    print("=" * 60)
    print("1. MemoryStore CRUD + search")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp))

        store.add(
            MemoryEntry(
                id="m1",
                user="srijith",
                session_id="conv-12",
                topic="Grief and loss of a friend",
                context="User dealing with sudden loss of a close friend",
                key_points=[
                    "Katha Upanishad 2.19 — the Self neither kills nor is killed",
                    "User found comfort in the teaching of impermanence",
                ],
                tags=["grief", "loss", "katha-upanishad"],
            )
        )
        store.add(
            MemoryEntry(
                id="m2",
                user="srijith",
                session_id="conv-15",
                topic="Career doubt and dharma",
                context="User questioning whether to leave stable job",
                key_points=[
                    "Gita 2.47 — you have a right to action, not to its fruits",
                    "Nishkama karma resonated with the user",
                ],
                tags=["career", "dharma", "gita"],
            )
        )
        store.add(
            MemoryEntry(
                id="m3",
                user="srijith",
                session_id="conv-28",
                topic="Letting go of attachment to outcomes",
                context="User struggling with attachment to a business outcome",
                key_points=["Isha Upanishad — enjoy without possessing"],
                tags=["attachment", "isha-upanishad", "letting-go"],
                related_sessions=["conv-15"],
            )
        )

        print(f"Total memories: {store.count()}")
        print(f"Memories for srijith: {store.count(user='srijith')}")
        print()

        for query in ["career dharma duty", "attachment letting go"]:
            print(f'Search: "{query}"')
            for entry, score in store.search(query, user="srijith"):
                print(f"  [{score:.3f}] {entry.topic} (session {entry.session_id})")
            print()

        status = store.get_status()
        print(f"Status: {status['total_entries']} entries, users: {status['users']}")
        print(f"Top tags: {status['top_tags'][:5]}")
    print()


def demo_context_injection() -> None:
    print("=" * 60)
    print("2. Context injection (what the agent sees)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp))
        store.add(
            MemoryEntry(
                id="m1",
                user="srijith",
                session_id="conv-12",
                topic="Grief and loss of a friend",
                context="User dealing with sudden loss of a close friend",
                key_points=[
                    "Katha Upanishad 2.19 resonated deeply",
                    "Found comfort in impermanence teaching",
                ],
            )
        )

        context = retrieve_context(
            user_input="grief is coming back, the loss of my friend",
            user="srijith",
            store=store,
        )
        if context:
            print(context)
        else:
            print("No relevant memories found")
    print()


async def demo_extraction() -> None:
    print("=" * 60)
    print("3. LLM insight extraction (mocked)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp))
        mock = AsyncMock()
        resp = AsyncMock()
        resp.text = (
            '{"topic": "Letting go of attachment", '
            '"context": "User struggling with attachment to business outcome", '
            '"key_points": ["Isha Upanishad — enjoy without possessing", '
            '"Connected to prior career dharma discussion"], '
            '"tags": ["attachment", "isha-upanishad", "letting-go"], '
            '"worth_saving": true}'
        )
        mock.complete.return_value = resp

        entry = await extract_and_save(
            user_input="How do I stop being attached to outcomes?",
            agent_output=(
                "The Isha Upanishad teaches tena tyaktena bhunjitha — "
                "enjoy through renunciation. This does not mean abandoning "
                "your business, but holding outcomes loosely."
            ),
            agent_id="advisor",
            session_id="conv-28",
            user="srijith",
            store=store,
            model_provider=mock,
            model_name="claude-haiku",
        )

        if entry:
            print(f"Topic:   {entry.topic}")
            print(f"Context: {entry.context}")
            print(f"Points:  {entry.key_points}")
            print(f"Tags:    {entry.tags}")
            print(f"User:    {entry.user}")
            print(f"Session: {entry.session_id}")
    print()


def demo_gbrain_page() -> None:
    print("=" * 60)
    print("4. GBrain page format")
    print("=" * 60)

    page = _build_memory_page(
        slug="memory/srijith/20260528T120000",
        topic="Letting go of attachment",
        context="User struggling with attachment to business outcome",
        key_points=[
            "Isha Upanishad — enjoy without possessing",
            "Connected to prior career dharma discussion",
        ],
        tags=["attachment", "isha-upanishad"],
        user="srijith",
        session_id="conv-28",
        agent_id="advisor",
    )
    print(page)
    print()


def demo_persistence() -> None:
    print("=" * 60)
    print("5. Persistence across restarts")
    print("=" * 60)

    tmp = tempfile.mkdtemp()
    p = Path(tmp)

    store1 = MemoryStore(p)
    store1.add(MemoryEntry(id="m1", user="srijith", topic="Grief discussion", tags=["grief"]))
    store1.add(MemoryEntry(id="m2", user="srijith", topic="Career dharma", tags=["career"]))
    print(f"Session 1: saved {store1.count()} memories")

    store2 = MemoryStore(p)
    print(f"Session 2 (after restart): loaded {store2.count()} memories")
    for e in store2.list_all():
        print(f"  - {e.topic} (tags: {e.tags})")

    shutil.rmtree(tmp)
    print()


def demo_user_deletion() -> None:
    print("=" * 60)
    print("6. User deletion (GDPR)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp))
        store.add(MemoryEntry(id="m1", user="alice", topic="Session 1"))
        store.add(MemoryEntry(id="m2", user="alice", topic="Session 2"))
        store.add(MemoryEntry(id="m3", user="bob", topic="Session 3"))
        print(
            f"Before: {store.count()} memories "
            f"(alice={store.count(user='alice')}, bob={store.count(user='bob')})"
        )

        removed = store.delete_user("alice")
        print(f"Deleted {removed} memories for alice")
        print(
            f"After: {store.count()} memories "
            f"(alice={store.count(user='alice')}, bob={store.count(user='bob')})"
        )


def main() -> None:
    demo_crud_and_search()
    demo_context_injection()
    asyncio.run(demo_extraction())
    demo_gbrain_page()
    demo_persistence()
    demo_user_deletion()
    print()
    print("All demos completed.")


if __name__ == "__main__":
    main()
