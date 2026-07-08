"""Delta state sync — diff/merge purity + the pull_state orchestrator (design 19 §delta sync)."""

from __future__ import annotations

from typing import Any

import pytest
from swarmkit_control_plane._delta import changed_refs, merge_state, pull_state
from swarmkit_control_plane._serve_client import ManifestUnsupported


def _entry(aid: str, h: str, content: Any = None) -> dict[str, Any]:
    return {"id": aid, "version": "1.0.0", "content_hash": h, "content": content}


def _state(
    topos: list[dict[str, Any]], skills: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "kind": "InstanceState",
        "workspace_id": "w",
        "schema_version": "1.7.0",
        "artifacts": {
            "topologies": topos,
            "skills": skills or [],
            "archetypes": [],
            "triggers": [],
        },
    }


# --- changed_refs -----------------------------------------------------------


def test_changed_refs_detects_changed_new_and_ignores_unchanged() -> None:
    cached = _state([_entry("a", "h1"), _entry("b", "h2")])
    manifest = _state([_entry("a", "h1"), _entry("b", "h2-NEW"), _entry("c", "h3")])
    refs = changed_refs(cached, manifest)
    # a unchanged (skipped); b changed; c new.
    assert set(refs) == {("topologies", "b"), ("topologies", "c")}


def test_changed_refs_empty_when_nothing_changed() -> None:
    cached = _state([_entry("a", "h1")], [_entry("s", "hs")])
    manifest = _state([_entry("a", "h1")], [_entry("s", "hs")])
    assert changed_refs(cached, manifest) == []


# --- merge_state ------------------------------------------------------------


def test_merge_reuses_cached_content_and_fills_fetched() -> None:
    cached = _state([_entry("a", "h1", {"v": "old-a"}), _entry("b", "h2", {"v": "old-b"})])
    manifest = _state([_entry("a", "h1"), _entry("b", "h2-NEW"), _entry("c", "h3")])
    fetched = _state([_entry("b", "h2-NEW", {"v": "new-b"}), _entry("c", "h3", {"v": "new-c"})])

    full, summary = merge_state(manifest, cached, fetched)
    by_id = {e["id"]: e for e in full["artifacts"]["topologies"]}
    assert by_id["a"]["content"] == {"v": "old-a"}  # reused from cache (unchanged)
    assert by_id["b"]["content"] == {"v": "new-b"}  # replaced by the fetched body
    assert by_id["c"]["content"] == {"v": "new-c"}  # new, fetched
    assert by_id["b"]["content_hash"] == "h2-NEW"  # manifest is authoritative for hash/version
    # 2 fetched (b, c), 1 reused (a), 0 removed.
    assert summary == {"fetched": 2, "reused": 1, "removed": 0}


def test_merge_drops_removed_artifacts_and_counts_them() -> None:
    cached = _state([_entry("a", "h1", {"v": "a"}), _entry("gone", "hx", {"v": "x"})])
    manifest = _state([_entry("a", "h1")])  # 'gone' no longer on the instance
    full, summary = merge_state(manifest, cached, _state([]))
    ids = [e["id"] for e in full["artifacts"]["topologies"]]
    assert ids == ["a"] and summary["removed"] == 1


def test_merge_preserves_metadata_envelope() -> None:
    cached = _state([_entry("a", "h1", {"v": "a"})])
    manifest = {**_state([_entry("a", "h1")]), "generated_at": "2026-07-08T00:00:00Z"}
    full, _ = merge_state(manifest, cached, _state([]))
    assert full["workspace_id"] == "w" and full["generated_at"] == "2026-07-08T00:00:00Z"
    assert "artifacts" in full


# --- pull_state orchestration ----------------------------------------------


@pytest.mark.asyncio
async def test_pull_first_sync_is_a_full_pull() -> None:
    full_state = _state([_entry("a", "h1", {"v": "a"})])

    async def fetch_state(e: str, t: str) -> dict[str, Any]:
        return full_state

    async def fetch_manifest(e: str, t: str) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("manifest should not be pulled on the first sync")

    async def fetch_artifacts(e: str, t: str, refs: Any) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError

    state, summary = await pull_state(
        endpoint="http://x",
        token_ref="",
        cached_state=None,
        fetch_state=fetch_state,
        fetch_manifest=fetch_manifest,
        fetch_artifacts=fetch_artifacts,
    )
    assert state == full_state and summary == {
        "mode": "full",
        "fetched": 1,
        "reused": 0,
        "removed": 0,
    }


@pytest.mark.asyncio
async def test_pull_delta_fetches_only_changed_bodies() -> None:
    cached = _state([_entry("a", "h1", {"v": "old-a"}), _entry("b", "h2", {"v": "old-b"})])
    manifest = _state([_entry("a", "h1"), _entry("b", "h2-NEW")])
    requested: list[list[tuple[str, str]]] = []

    async def fetch_state(e: str, t: str) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("full pull should not run when a manifest diff suffices")

    async def fetch_manifest(e: str, t: str) -> dict[str, Any]:
        return manifest

    async def fetch_artifacts(e: str, t: str, refs: list[tuple[str, str]]) -> dict[str, Any]:
        requested.append(refs)
        return _state([_entry("b", "h2-NEW", {"v": "new-b"})])

    state, summary = await pull_state(
        endpoint="http://x",
        token_ref="",
        cached_state=cached,
        fetch_state=fetch_state,
        fetch_manifest=fetch_manifest,
        fetch_artifacts=fetch_artifacts,
    )
    assert requested == [[("topologies", "b")]]  # only the changed body was fetched
    assert summary == {"mode": "delta", "fetched": 1, "reused": 1, "removed": 0}
    by_id = {e["id"]: e["content"] for e in state["artifacts"]["topologies"]}
    assert by_id == {"a": {"v": "old-a"}, "b": {"v": "new-b"}}


@pytest.mark.asyncio
async def test_pull_delta_with_no_changes_skips_the_body_fetch() -> None:
    cached = _state([_entry("a", "h1", {"v": "a"})])

    async def fetch_state(e: str, t: str) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError

    async def fetch_manifest(e: str, t: str) -> dict[str, Any]:
        return _state([_entry("a", "h1")])

    called = False

    async def fetch_artifacts(e: str, t: str, refs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"artifacts": {}}

    _, summary = await pull_state(
        endpoint="http://x",
        token_ref="",
        cached_state=cached,
        fetch_state=fetch_state,
        fetch_manifest=fetch_manifest,
        fetch_artifacts=fetch_artifacts,
    )
    assert not called  # nothing changed → no body fetch at all
    assert summary == {"mode": "delta", "fetched": 0, "reused": 1, "removed": 0}


@pytest.mark.asyncio
async def test_pull_falls_back_to_full_when_manifest_unsupported() -> None:
    full_state = _state([_entry("a", "h1", {"v": "a"})])

    async def fetch_state(e: str, t: str) -> dict[str, Any]:
        return full_state

    async def fetch_manifest(e: str, t: str) -> dict[str, Any]:
        raise ManifestUnsupported("pre-delta serve")

    async def fetch_artifacts(e: str, t: str, refs: Any) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError

    state, summary = await pull_state(
        endpoint="http://x",
        token_ref="",
        cached_state=_state([]),  # has a cache, but the instance can't serve a manifest
        fetch_state=fetch_state,
        fetch_manifest=fetch_manifest,
        fetch_artifacts=fetch_artifacts,
    )
    assert state == full_state and summary["mode"] == "full-fallback"
