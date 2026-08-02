"""A store URL is masked when DISPLAYED and intact when FORWARDED.

Two mirror-image bugs, fixed together so the rule is stated once:

* 1.127.1 — `str(engine.url)` masked the password in a place it had to be intact, so the artifact
  store authenticated with the literal text `***` and Postgres rejected it;
* reported 2026-07 — `swarmkit orchestrator` printed the store URL unredacted on startup, putting
  the database password in terminal scrollback, any redirected log, and CI capture.

Truncation is not a third option: `url[:30]`, which serve used, leaks a password *prefix*.
"""

from __future__ import annotations

import re
from pathlib import Path

from swarmkit_runtime.persistence._store import engine_url, make_engine, redacted_url

PG = "postgresql+psycopg://swarm:hunter2secret@127.0.0.1:5433/swarmkit"


def test_display_masks_the_password() -> None:
    shown = redacted_url(PG)
    assert "hunter2secret" not in shown
    assert "***" in shown
    # Still useful: host, port, database and user survive, which is what a log line is for.
    assert "swarm" in shown and "127.0.0.1:5433" in shown and "swarmkit" in shown


def test_forwarding_keeps_the_password() -> None:
    """The mirror. Both must hold, or one of the two bugs comes back."""
    assert engine_url(make_engine(PG)) == PG


def test_truncation_would_have_leaked_a_prefix() -> None:
    """Pins why truncation was replaced rather than lengthened."""
    assert "hun" in PG[:30], "the old url[:30] exposed the start of the password"
    assert "hun" not in redacted_url(PG).split("@")[0].replace("swarm", "")


def test_a_passwordless_url_is_unchanged_in_substance() -> None:
    url = "postgresql+psycopg://swarm@127.0.0.1:5433/swarmkit"
    assert "swarm" in redacted_url(url) and "127.0.0.1" in redacted_url(url)


def test_sqlite_round_trips(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'store.sqlite'}"
    assert redacted_url(url) == url


def test_an_unparseable_url_is_not_echoed_back() -> None:
    """A typo'd URL might still contain a secret; never print it verbatim."""
    assert redacted_url("postgres://:::not a url:::") == "<unparseable store url>"


def test_no_caller_displays_a_raw_store_url() -> None:
    """Guards the rule at the two sites that print one."""
    root = Path(__file__).resolve().parents[1] / "src/swarmkit_runtime"
    orchestrator = (root / "cli/_cmd_orchestrator.py").read_text()
    assert "redacted_url(db)" in orchestrator
    assert not re.search(r"driving events from \{db\}", orchestrator)

    factory = (root / "persistence/_factory.py").read_text()
    assert "redacted_url(url)" in factory
    # The comment explaining the old bug names `url[:30]` deliberately; match a logging ARGUMENT.
    assert not re.search(r",\s*url\[:\d+\]", factory)
