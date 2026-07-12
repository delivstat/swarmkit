"""Trust accrual — turn repeated human approvals of a relayed capability into a reviewed allowlist
changeset (executor-trust-accrual-plan.md, RFC §6.2.3 / decision 6, "P3.5").

Relay makes a human approve an out-of-grant capability *every* run. This store watches those
decisions per ``(archetype, capability)`` pair and, after enough consecutive approvals with no
denial, **proposes** adding the capability to the archetype's allowlist — the operator approves
once and future runs stop asking. It only *proposes*; it never widens a grant on its own (invariant
§8.7 — human-only scope). A single deliberate **denial resets the count and blocks** the pair until
an operator clears it: one "no" is a signal, not noise.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

_DEFAULT_THRESHOLD = 5
_KEY_SEP = "\x1f"  # unit separator — safe against capabilities containing any printable char


@dataclass(frozen=True)
class TrustRecord:
    """Accrued evidence for one ``(archetype, capability)`` pair."""

    archetype: str
    capability: str
    approvals: int = 0
    blocked: bool = False  # a denial was seen; no proposal until an operator clears it
    proposed: bool = False  # the changeset has been proposed (avoid re-proposing every run)
    applied: bool = False  # the operator applied it — the capability is now allowlisted


@dataclass(frozen=True)
class TrustProposal:
    """A pending allowlist changeset the operator can apply — made the run the count crosses N."""

    archetype: str
    capability: str
    approvals: int


class TrustStore:
    """File-backed accrual state under ``.swarmkit/trust-accrual.json`` — one JSON object keyed by
    ``archetype \\x1f capability``. Pure + synchronous; the relay orchestrator calls ``record`` and,
    on a fresh proposal, audits it."""

    def __init__(self, base_dir: Path, *, threshold: int = _DEFAULT_THRESHOLD) -> None:
        self._path = base_dir / ".swarmkit" / "trust-accrual.json"
        self._threshold = max(1, threshold)

    # -- reads -----------------------------------------------------------------------------------

    def _load(self) -> dict[str, TrustRecord]:
        if not self._path.exists():
            return {}
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return {k: TrustRecord(**v) for k, v in raw.items()}

    def _save(self, records: dict[str, TrustRecord]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: asdict(v) for k, v in records.items()}
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def proposals(self) -> list[TrustProposal]:
        """Every proposed-but-not-yet-applied changeset, oldest key first."""
        return [
            TrustProposal(r.archetype, r.capability, r.approvals)
            for _, r in sorted(self._load().items())
            if r.proposed and not r.applied
        ]

    # -- writes ----------------------------------------------------------------------------------

    def record(self, archetype: str, capability: str, granted: bool) -> TrustProposal | None:
        """Fold one **operator** decision into the pair's tally. Returns a :class:`TrustProposal`
        exactly once — the call that crosses the threshold — else ``None``.

        - approved ⇒ ``approvals += 1``; at the threshold (and not blocked/proposed) ⇒ a proposal.
        - denied ⇒ ``approvals = 0`` and ``blocked = True`` (until :meth:`clear`).
        """
        records = self._load()
        key = _key(archetype, capability)
        cur = records.get(key, TrustRecord(archetype, capability))

        if not granted:
            records[key] = TrustRecord(archetype, capability, approvals=0, blocked=True)
            self._save(records)
            return None

        approvals = cur.approvals + 1
        crossed = approvals >= self._threshold and not cur.blocked and not cur.proposed
        records[key] = TrustRecord(
            archetype,
            capability,
            approvals=approvals,
            blocked=cur.blocked,
            proposed=cur.proposed or crossed,
            applied=cur.applied,
        )
        self._save(records)
        return TrustProposal(archetype, capability, approvals) if crossed else None

    def apply(self, archetype: str, capability: str) -> bool:
        """Mark a proposal applied (the operator added it to the allowlist). Idempotent; ``False``
        if there is no proposal for the pair."""
        records = self._load()
        key = _key(archetype, capability)
        rec = records.get(key)
        if rec is None or not rec.proposed:
            return False
        records[key] = TrustRecord(
            archetype, capability, approvals=rec.approvals, proposed=True, applied=True
        )
        self._save(records)
        return True

    def clear(self, archetype: str, capability: str) -> bool:
        """Lift a denial block and reset the tally so the pair can accrue again. ``False`` if the
        pair is unknown."""
        records = self._load()
        key = _key(archetype, capability)
        if key not in records:
            return False
        records[key] = TrustRecord(archetype, capability)
        self._save(records)
        return True


def _key(archetype: str, capability: str) -> str:
    return f"{archetype}{_KEY_SEP}{capability}"


__all__ = ["TrustProposal", "TrustRecord", "TrustStore"]
