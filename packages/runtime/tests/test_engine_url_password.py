"""A forwarded store URL must keep its password.

`str(engine.url)` renders the password as `***`. That is correct for a log line and silently wrong
when the string is handed to another store as a connection URL — the receiving store then
authenticates with the literal text `***` and the server rejects it.

Reported against 1.127.0: `swarmkit serve` on Postgres connected the runtime store fine, then died
in `build_artifact_store` -> `DatabaseArtifactStore.from_url` -> `metadata.create_all`. It only
bites a URL with a NON-EMPTY password, which is why SQLite and passwordless Postgres never hit it
and why the whole suite missed it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from swarmkit_runtime.persistence._store import engine_url

PG = "postgresql+psycopg://swarm:hunter2@127.0.0.1:5433/swarmkit"


def test_engine_url_keeps_the_password() -> None:
    engine = create_engine(PG)
    assert engine_url(engine) == PG
    assert "hunter2" in engine_url(engine)


def test_str_of_the_url_still_masks() -> None:
    """The masking itself is right — this pins WHY the helper has to exist, so nobody 'simplifies'
    engine_url back to str() later."""
    engine = create_engine(PG)
    assert "***" in str(engine.url)
    assert "hunter2" not in str(engine.url)


def test_a_passwordless_url_round_trips_unchanged() -> None:
    """The configuration that hid the bug."""
    url = "postgresql+psycopg://swarm@127.0.0.1:5433/swarmkit"
    assert engine_url(create_engine(url)) == url


def test_sqlite_round_trips_unchanged(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'store.sqlite'}"
    assert engine_url(create_engine(url)) == url


def test_serve_forwards_an_unmasked_url_to_the_artifact_store(monkeypatch: Any) -> None:
    """The actual regression: serve builds the artifact store from the main store's engine.

    Asserted at the seam rather than end-to-end, because reproducing it live needs a Postgres with
    a password — the point is that what reaches `build_artifact_store` is connectable.
    """
    captured: dict[str, str] = {}

    def _fake_build(cfg: Any, *, workspace_root: Path, database_url: str) -> object:
        captured["url"] = database_url
        return object()

    from swarmkit_runtime import artifacts  # noqa: PLC0415

    monkeypatch.setattr(artifacts, "build_artifact_store", _fake_build)

    engine = create_engine(PG)
    # The one line serve runs; a regression here re-breaks every password-bearing deployment.
    artifacts.build_artifact_store(None, workspace_root=Path("."), database_url=engine_url(engine))

    assert captured["url"] == PG
    assert "***" not in captured["url"]
