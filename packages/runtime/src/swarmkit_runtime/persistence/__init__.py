"""Persistence. Everything that opens a store goes through :class:`StorageService`.

``create_store`` remains for embedders that already call it; it is a thin wrapper over the
service now, so it cannot disagree with the rest of the process about where data lives.
"""

from swarmkit_runtime.persistence._eval_runs import (
    FINISHED_EVAL_STATUSES,
    RESUMABLE_EVAL_STATUSES,
    EvalRunRow,
)
from swarmkit_runtime.persistence._factory import create_store
from swarmkit_runtime.persistence._service import (
    StorageConfigError,
    StorageService,
    StoreKind,
    StoreTarget,
    storage_for_workspace,
)
from swarmkit_runtime.persistence._store import SqliteStore, Store, UsageRow
from swarmkit_runtime.persistence._usage_recording import record_run_usage, usage_fields

__all__ = [
    "FINISHED_EVAL_STATUSES",
    "RESUMABLE_EVAL_STATUSES",
    "EvalRunRow",
    "SqliteStore",
    "StorageConfigError",
    "StorageService",
    "Store",
    "StoreKind",
    "StoreTarget",
    "UsageRow",
    "create_store",
    "record_run_usage",
    "storage_for_workspace",
    "usage_fields",
]
