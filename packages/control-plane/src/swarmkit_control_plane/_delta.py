"""Delta state sync — pull only the artifacts whose content changed (design 19 §delta sync).

The full ``InstanceState`` is sizable for a large workspace, so after the first (full) sync the
panel pulls the cheap names-only **manifest**, diffs each artifact's ``content_hash`` against its
cache, fetches only the **changed** bodies, and merges them back over the cached content. If an
instance predates the manifest endpoint the connector raises ``ManifestUnsupported`` and we fall
back to a full pull — delta sync degrades gracefully.

The diff/merge are pure functions (unit-tested without HTTP); ``pull_state`` orchestrates them over
injected fetch callables.
"""

from __future__ import annotations

from typing import Any

from swarmkit_control_plane._fntypes import StateArtifactsFn, StateFn, StateManifestFn
from swarmkit_control_plane._serve_client import ManifestUnsupported


def _entries(state: dict[str, Any], collection: str) -> list[dict[str, Any]]:
    artifacts = state.get("artifacts", {}) if isinstance(state, dict) else {}
    items = artifacts.get(collection, [])
    return items if isinstance(items, list) else []


def _index_content(state: dict[str, Any]) -> dict[tuple[str, str], Any]:
    """``(collection, id) -> content`` over every artifact in a state."""
    out: dict[tuple[str, str], Any] = {}
    for collection, entries in (state.get("artifacts", {}) or {}).items():
        for entry in entries or []:
            if isinstance(entry, dict) and "id" in entry:
                out[(collection, entry["id"])] = entry.get("content")
    return out


def changed_refs(cached_state: dict[str, Any], manifest: dict[str, Any]) -> list[tuple[str, str]]:
    """``(collection, id)`` pairs whose manifest ``content_hash`` differs from cache (or are new).

    Unchanged artifacts (same hash) and removed ones (absent from the manifest) are not fetched."""
    refs: list[tuple[str, str]] = []
    for collection, entries in (manifest.get("artifacts", {}) or {}).items():
        cached_hash = {
            e["id"]: e.get("content_hash") for e in _entries(cached_state, collection) if "id" in e
        }
        for entry in entries or []:
            if not isinstance(entry, dict) or "id" not in entry:
                continue
            if entry.get("content_hash") != cached_hash.get(entry["id"]):
                refs.append((collection, entry["id"]))
    return refs


def merge_state(
    manifest: dict[str, Any],
    cached_state: dict[str, Any],
    fetched_state: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    """Rebuild the full ``InstanceState`` from the manifest structure, filling each artifact's
    content from the freshly-**fetched** bodies where present, else **reused** from the cache. The
    manifest is authoritative for which artifacts exist, so artifacts dropped on the instance fall
    away naturally.

    Returns ``(full_state, summary)`` where ``summary`` counts fetched / reused / removed.
    """
    fetched = _index_content(fetched_state)
    cached = _index_content(cached_state)

    full: dict[str, Any] = {k: v for k, v in manifest.items() if k != "artifacts"}
    merged_artifacts: dict[str, list[dict[str, Any]]] = {}
    fetched_n = reused_n = 0
    for collection, entries in (manifest.get("artifacts", {}) or {}).items():
        merged: list[dict[str, Any]] = []
        for entry in entries or []:
            key = (collection, entry.get("id"))
            if key in fetched:
                content = fetched[key]
                fetched_n += 1
            else:
                content = cached.get(key)
                reused_n += 1
            merged.append({**entry, "content": content})
        merged_artifacts[collection] = merged
    full["artifacts"] = merged_artifacts

    manifest_keys = {
        (collection, e.get("id"))
        for collection, entries in (manifest.get("artifacts", {}) or {}).items()
        for e in (entries or [])
    }
    removed_n = sum(1 for key in cached if key not in manifest_keys)
    return full, {"fetched": fetched_n, "reused": reused_n, "removed": removed_n}


def _full_summary(state: dict[str, Any]) -> dict[str, int]:
    total = sum(len(entries or []) for entries in (state.get("artifacts", {}) or {}).values())
    return {"fetched": total, "reused": 0, "removed": 0}


async def pull_state(
    *,
    endpoint: str,
    token_ref: str,
    cached_state: dict[str, Any] | None,
    fetch_state: StateFn,
    fetch_manifest: StateManifestFn,
    fetch_artifacts: StateArtifactsFn,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Sync an instance's state, transferring only what changed. First sync (no cache) or a
    pre-delta instance → full pull; otherwise manifest-diff-fetch-merge. Returns
    ``(state, summary)``; ``summary["mode"]`` is ``full`` / ``full-fallback`` / ``delta``."""
    if cached_state is None:
        state = await fetch_state(endpoint, token_ref)
        return state, {"mode": "full", **_full_summary(state)}

    try:
        manifest = await fetch_manifest(endpoint, token_ref)
    except ManifestUnsupported:
        state = await fetch_state(endpoint, token_ref)
        return state, {"mode": "full-fallback", **_full_summary(state)}

    refs = changed_refs(cached_state, manifest)
    fetched_state = await fetch_artifacts(endpoint, token_ref, refs) if refs else {"artifacts": {}}
    state, summary = merge_state(manifest, cached_state, fetched_state)
    return state, {"mode": "delta", **summary}


__all__ = ["changed_refs", "merge_state", "pull_state"]
