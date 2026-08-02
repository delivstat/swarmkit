"""The single source of truth for where data lives (design/details/storage-service.md).

Before this, six components each constructed their own store and four ignored configuration: three
hardcoded a SQLite path, and one called the resolver without the workspace config. A workspace
declaring Postgres for every store ran entirely on a local file — sagas, audit trail and governed
memory — with no error at any point, because the only symptom was an empty screen.

So: components ask this service for a **kind**, never a path. Resolution happens once, engines are
cached per URL, and every choice is reported at startup.

Rules worth stating, because each one is a bug that happened:

* **A URL implies its backend.** ``SWARMKIT_STORE_URL=postgresql://…`` alone is enough; requiring
  ``SWARMKIT_STORE_BACKEND`` beside it meant a correctly-set URL was silently ignored.
* **A backend that cannot be honoured raises.** Degrading to SQLite writes the run to a different
  database than the one configured, which is worse than failing to start (the rule from 1.127.0).
* **Nothing outside this package opens a store.** Enforced by a test, because it regressed three
  times.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from swarmkit_runtime.persistence._store import SqliteStore, Store, make_engine, redacted_url

logger = logging.getLogger("swarmkit.persistence")


_SERVICES: dict[Path, StorageService] = {}


def storage_for_workspace(root: Path | str, workspace_raw: Any = None) -> StorageService:
    """The storage service for a workspace — one per root, per process.

    When *workspace_raw* is omitted this loads ``workspace.yaml`` itself. That matters: the old
    ``audit_provider_for_path(root)`` took only a path, so it could not see ``storage.audit`` even
    when the workspace declared it, and every caller that had a path but not the parsed config
    silently got SQLite. A caller should not have to hold the config to get the right database.
    """
    key = Path(root).resolve()
    cached = _SERVICES.get(key)
    if cached is not None:
        return cached
    raw = workspace_raw if workspace_raw is not None else _load_workspace_raw(key)
    service = StorageService(key, raw)
    _SERVICES[key] = service
    return service


def _load_workspace_raw(root: Path) -> Any:
    """Best-effort read of the workspace config. A workspace that will not parse is a problem for
    the loader to report, not a reason for storage resolution to explode."""
    for name in ("workspace.yaml", "workspace.yml"):
        path = root / name
        if not path.exists():
            continue
        try:
            import yaml  # noqa: PLC0415

            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:  # see docstring: a bad workspace is the loader's error to report
            logger.debug("could not read %s for storage config", path, exc_info=True)
            return None
    return None


def reset_storage_cache() -> None:
    """Drop cached services. For tests, which build many workspaces in one process."""
    _SERVICES.clear()


class StorageConfigError(RuntimeError):
    """The storage config names something that cannot be honoured."""


class StoreKind(StrEnum):
    """What a component wants, expressed as a purpose rather than a location."""

    RUNTIME = "runtime"  # jobs, conversations, usage
    AUDIT = "audit"
    CHECKPOINTS = "checkpoints"
    ARTIFACTS = "artifacts"
    MEMORY = "memory"
    SAGA = "saga"
    FLEET = "fleet"


#: Kinds with their own `storage.<kind>` block. Everything else follows `storage.runtime`.
_OWN_BLOCK = {StoreKind.AUDIT, StoreKind.CHECKPOINTS}

#: Kinds that do NOT inherit `storage.runtime`. The checkpointer is a LangGraph component with its
#: own driver requirement, so promoting it to Postgres merely because the application store is
#: Postgres would fail a workspace that never asked for it — and the failure would arrive at
#: startup, on config the operator did not write.
_NO_INHERIT = {StoreKind.CHECKPOINTS}

#: Fleet membership follows `storage.runtime` like everything else — design 19 Q4 settled that,
#: and the shipped `create_membership_store` implements it. Its own SQLite FILE, not its own
#: backend: enrollment rows do not belong in the same table space as jobs.
_ALWAYS_LOCAL: set[StoreKind] = set()

_SQLITE_FILE = {
    StoreKind.RUNTIME: "store.sqlite",
    StoreKind.SAGA: "store.sqlite",  # coexisting tables, one file
    StoreKind.ARTIFACTS: "store.sqlite",
    StoreKind.MEMORY: "store.sqlite",
    StoreKind.AUDIT: "audit.sqlite",
    StoreKind.FLEET: "fleet.sqlite",
    StoreKind.CHECKPOINTS: "state/checkpoints.db",
}


class StoreTarget:
    """A resolved destination: the backend, its URL, and where that decision came from."""

    __slots__ = ("backend", "source", "url")

    def __init__(self, backend: str, url: str, source: str) -> None:
        self.backend = backend
        self.url = url
        self.source = source

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"StoreTarget({self.backend!r}, {redacted_url(self.url)!r}, {self.source!r})"


def _field(obj: Any, key: str) -> Any:
    """Read *key* off a parsed-YAML Mapping **or** a typed model — both shapes reach here."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _as_dict(value: Any) -> dict[str, Any] | None:
    """A config block as a plain dict. ``raw`` is a pydantic model under serve and parsed YAML
    under the CLI, and the artifact-store builder takes a Mapping — so normalise here rather than
    making every consumer handle both."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    return dict(dump(exclude_none=True)) if callable(dump) else None


def _text(value: Any) -> str:
    """Normalise a config scalar. The schema models `backend` as an ENUM, and
    ``Backend2.postgres`` is not a ``str`` — comparing it to ``"postgres"`` is always False, which
    is how every workspace silently ran on SQLite before 1.127.0."""
    if value is None:
        return ""
    inner = getattr(value, "value", value)
    return str(inner).strip().lower()


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def expand_env(value: str) -> str:
    """Expand ``${VAR}``, ``${VAR:-default}`` and ``$VAR`` in a config string.

    Nothing else in the runtime does this, so ``url: ${SWARMKIT_STORE_URL}`` — the form every
    example and every deployment doc uses — reached SQLAlchemy as those literal 21 characters.
    An unset variable expands to empty, which then trips the no-URL error with a message naming
    the setting, rather than a driver error naming a host called ``${SWARMKIT_STORE_URL}``.
    """

    def _one(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(3)
        default = match.group(2)
        return os.environ.get(name, default if default is not None else "")

    return _ENV_REF.sub(_one, value)


def backend_from_url(url: str) -> str:
    """The backend a URL names. A URL is unambiguous about its own dialect."""
    lowered = url.strip().lower()
    if lowered.startswith(("postgres://", "postgresql://", "postgresql+")):
        return "postgres"
    if lowered.startswith("sqlite"):
        return "sqlite"
    return ""


class StorageService:
    """Resolves every store for one workspace, and owns the engines."""

    def __init__(self, root: Path, workspace_raw: Any = None) -> None:
        self._root = Path(root).resolve()
        self._raw = workspace_raw
        self._targets: dict[StoreKind, StoreTarget] = {}
        self._engines: dict[str, Engine] = {}

    @classmethod
    def for_workspace(cls, root: Path | str, workspace_raw: Any = None) -> StorageService:
        return cls(Path(root), workspace_raw)

    # ---- resolution ----------------------------------------------------------------------------

    def target(self, kind: StoreKind) -> StoreTarget:
        """The resolved destination for *kind*, computed once."""
        cached = self._targets.get(kind)
        if cached is not None:
            return cached
        resolved = self._resolve(kind)
        self._targets[kind] = resolved
        return resolved

    def _resolve(self, kind: StoreKind) -> StoreTarget:
        if kind in _ALWAYS_LOCAL:
            return StoreTarget("sqlite", self._sqlite_url(kind), "separate by design")

        block = _field(self._raw, "storage")
        own = _field(block, kind.value) if kind in _OWN_BLOCK else None
        runtime_cfg = _field(block, "runtime")

        # SWARMKIT_STORE_URL is a GLOBAL "the store is over there" signal, so it is exactly as
        # wrong for checkpoints as inheriting `storage.runtime` would be: setting it to move the
        # application store must not drag the LangGraph checkpointer along and fail on a missing
        # driver. Only an explicit `storage.checkpoints` block decides this one, either way.
        env_applies = kind not in _NO_INHERIT

        env_backend = os.environ.get("SWARMKIT_STORE_BACKEND", "").strip().lower()
        env_url = os.environ.get("SWARMKIT_STORE_URL") or os.environ.get("DATABASE_URL", "")
        if env_applies and (env_backend or env_url):
            # A URL alone is sufficient — this is the case that used to fall through to SQLite.
            backend = env_backend or backend_from_url(env_url)
            if backend:
                return self._checked(kind, backend, env_url, "env")

        candidates = [(own, f"storage.{kind.value}")]
        if kind not in _NO_INHERIT:
            candidates.append((runtime_cfg, "storage.runtime"))
        for cfg, source in candidates:
            backend = _text(_field(cfg, "backend"))
            if not backend:
                continue
            # A per-kind block inherits `storage.runtime.url` when it declares none — repeating the
            # same URL under three keys is what made the config look honoured when it was not.
            raw_url = _field(cfg, "url") or _field(runtime_cfg, "url")
            url = expand_env(str(getattr(raw_url, "value", raw_url) or "")).strip()
            return self._checked(kind, backend, url, source)

        return StoreTarget("sqlite", self._sqlite_url(kind), "default")

    def _checked(self, kind: StoreKind, backend: str, url: str, source: str) -> StoreTarget:
        """Validate a resolved target, raising rather than degrading."""
        if backend == "sqlite":
            return StoreTarget("sqlite", url or self._sqlite_url(kind), source)
        if backend != "postgres":
            raise StorageConfigError(
                f"storage backend {backend!r} for {kind.value} (from {source}) is not supported. "
                "Use 'sqlite' or 'postgres'."
            )
        if not url:
            settings = dict.fromkeys(
                [f"storage.{kind.value}.url", "storage.runtime.url", "SWARMKIT_STORE_URL"]
            )
            raise StorageConfigError(
                f"storage backend 'postgres' for {kind.value} (from {source}) has no URL. Set one "
                f"of: {', '.join(settings)}. (If the value is '${{VAR}}', that variable is unset.) "
                "Refusing to fall back to sqlite: the run would write to a different database "
                "than the one configured."
            )
        if kind is StoreKind.CHECKPOINTS and not _postgres_checkpointer_available():
            # Degrade, loudly — the one place that is right, and only here.
            #
            # "Refuse rather than degrade" protects RECORDS: a run writing its audit trail or its
            # governed memory to a different database than the configured one loses data silently,
            # and no error is cheaper than that. Checkpoints are not records. They are disposable
            # run state, and putting them in the local file costs resumability from another host —
            # a bounded failure that surfaces at resume, on the run it affects.
            #
            # This is also a missing OPTIONAL DEPENDENCY, not a wrong config. Refusing to boot
            # over one takes down serve, the orchestrator and every trigger — for a store whose
            # contents can be thrown away. And it punished exactly the workspaces this change set
            # out to help: `storage.checkpoints.backend: postgres` was silently ignored before
            # 1.130.0, so every workspace that wrote it upgraded straight into a dead server.
            logger.warning(
                "storage.checkpoints.backend is 'postgres' (from %s) but the Postgres "
                "checkpointer is not installed — using the local SQLite checkpointer instead. "
                "Runs stay resumable on THIS host only. Install it with:  "
                "pip install 'swarmkit-runtime[postgres]'  (or set "
                "storage.checkpoints.backend: sqlite to silence this).",
                source,
            )
            return StoreTarget(
                "sqlite",
                self._sqlite_url(kind),
                f"{source} → sqlite (postgres extra not installed)",
            )
        return StoreTarget("postgres", url, source)

    def _sqlite_url(self, kind: StoreKind) -> str:
        path = self._root / ".swarmkit" / _SQLITE_FILE[kind]
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{path}"

    # ---- engines -------------------------------------------------------------------------------

    def engine(self, kind: StoreKind) -> Engine:
        """The engine for *kind*, shared with every other kind resolving to the same URL — one
        database means one connection pool, not one per component."""
        url = self.target(kind).url
        existing = self._engines.get(url)
        if existing is None:
            existing = make_engine(url)
            self._engines[url] = existing
        return existing

    # ---- the stores ----------------------------------------------------------------------------

    def store(self) -> Store:
        """Jobs, conversations, usage."""
        target = self.target(StoreKind.RUNTIME)
        if target.backend == "sqlite":
            return SqliteStore(self._root)
        return Store(self.engine(StoreKind.RUNTIME))

    def saga_store(self) -> Any:
        from swarmkit_runtime.orchestration import SqlSagaStore  # noqa: PLC0415

        return SqlSagaStore(self.engine(StoreKind.SAGA))

    def artifact_store(self) -> Any:
        from swarmkit_runtime.artifacts import build_artifact_store  # noqa: PLC0415

        storage_cfg = _field(self._raw, "storage")
        return build_artifact_store(
            _as_dict(_field(storage_cfg, "artifacts")),
            workspace_root=self._root,
            database_url=self.target(StoreKind.ARTIFACTS).url,
        )

    def audit_provider(self) -> Any:
        from swarmkit_runtime.audit import SqlAuditProvider  # noqa: PLC0415

        retention = _field(_field(_field(self._raw, "storage"), "audit"), "retention_days")
        return SqlAuditProvider(self.engine(StoreKind.AUDIT), retention_days=int(retention or 365))

    def memory_store(self, **kwargs: Any) -> Any:
        from swarmkit_runtime.governed_memory import GovernedMemoryStore  # noqa: PLC0415

        return GovernedMemoryStore(self.engine(StoreKind.MEMORY), **kwargs)

    def membership_store(self) -> Any:
        from swarmkit_runtime.fleet._store import MembershipStore  # noqa: PLC0415

        return MembershipStore(self.engine(StoreKind.FLEET))

    def checkpointer(self) -> Any:
        target = self.target(StoreKind.CHECKPOINTS)
        if target.backend == "postgres":
            from langgraph.checkpoint.postgres import PostgresSaver  # noqa: PLC0415

            saver = PostgresSaver.from_conn_string(target.url)
            saver.setup()
            return saver
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: PLC0415
        except ImportError:
            from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

            return MemorySaver()
        return SqliteSaver.from_conn_string(target.url.removeprefix("sqlite:///"))

    # ---- what it chose -------------------------------------------------------------------------

    def report(self) -> list[str]:
        """One line per kind. The absence of this is why a misconfigured workspace looked like an
        empty one rather than a misrouted one."""
        lines = []
        for kind in StoreKind:
            t = self.target(kind)
            where = redacted_url(t.url) if t.backend == "postgres" else "workspace-local"
            lines.append(f"  {kind.value:<12} {t.backend:<9} {where}  ({t.source})")
        return lines

    def log_report(self) -> None:
        logger.info("storage:\n%s", "\n".join(self.report()))
        for line in self.split_warnings():
            logger.warning("%s", line)

    def split_warnings(self) -> list[str]:
        """Warn when a kind is configured remote but a populated local SQLite exists for it.

        Anyone upgrading into this change has rows in the old place, and a silent cutover looks
        exactly like data loss.
        """
        out: list[str] = []
        for kind in StoreKind:
            target = self.target(kind)
            if target.backend != "postgres":
                continue
            local = self._root / ".swarmkit" / _SQLITE_FILE[kind]
            rows = _sqlite_row_estimate(local)
            if rows:
                out.append(
                    f"{kind.value}: configured for postgres, but {local} still holds ~{rows} "
                    f"rows written before this. Move them with:  swarmkit storage migrate "
                    f"{self._root}"
                )
        return out


def _postgres_checkpointer_available() -> bool:
    from importlib.util import find_spec  # noqa: PLC0415

    return find_spec("langgraph.checkpoint.postgres") is not None


def _sqlite_row_estimate(path: Path) -> int:
    """Rough row count across a SQLite file's tables; 0 when absent or unreadable."""
    if not path.is_file():
        return 0
    import sqlite3  # noqa: PLC0415

    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return 0
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        total = 0
        for table in tables:
            try:
                total += int(conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
            except sqlite3.Error:  # pragma: no cover - a table we cannot read is not a blocker
                continue
        return total
    finally:
        conn.close()


__all__ = [
    "StorageConfigError",
    "StorageService",
    "StoreKind",
    "StoreTarget",
    "backend_from_url",
]
