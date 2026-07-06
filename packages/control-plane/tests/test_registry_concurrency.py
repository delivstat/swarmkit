"""claim_queued must be atomic even across processes — no command dispatched twice.

Each worker uses its *own* ``SqliteRegistry`` (own engine → own connection + in-process lock) on the
same database file, so the in-process lock can't serialise them; only SQLite's write lock (the
``BEGIN IMMEDIATE`` escalation in ``claim_queued``) prevents two concurrent claims from grabbing the
same queued command. This is the cross-process guard against double-dispatch.
"""

from __future__ import annotations

import threading
from collections import Counter
from pathlib import Path

from swarmkit_control_plane._registry import SqliteRegistry


def test_concurrent_claims_never_double_dispatch(tmp_path: Path) -> None:
    db = tmp_path / "registry.sqlite"
    seed = SqliteRegistry(db)
    n_cmds = 60
    for i in range(n_cmds):
        seed.enqueue("edge", "capabilities", {"i": i})

    workers = 8
    claimed: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(workers)

    def claim() -> None:
        reg = SqliteRegistry(db)  # separate engine → separate in-process lock
        barrier.wait()  # maximise contention
        got: list[str] = []
        while True:
            batch = reg.claim_queued("edge", limit=5)
            if not batch:
                break
            got.extend(c.cmd_id for c in batch)
        with lock:
            claimed.extend(got)

    threads = [threading.Thread(target=claim) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    counts = Counter(claimed)
    dupes = {cid: n for cid, n in counts.items() if n > 1}
    assert not dupes, f"commands double-dispatched: {dupes}"
    assert len(claimed) == n_cmds  # every command claimed exactly once
