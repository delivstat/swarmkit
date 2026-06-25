"""Resolve a serve-auth ``key_ref`` to a concrete secret string.

A ``key_ref`` is never the literal secret in committed config. Supported schemes:

- ``env:VAR``        — read environment variable ``VAR``.
- ``file:/path``     — read the file's contents (trailing whitespace stripped). Covers
  Docker/k8s mounted secrets and the common Vault-agent-writes-to-a-file pattern.
- ``credentials:NAME`` — resolve the workspace ``credentials`` entry ``NAME`` by its
  ``source``. ``env`` and ``file`` sources are resolved here; native cloud/vault backends
  (hashicorp-vault, aws/gcp/azure, plugin) raise NotImplementedError until a SecretsProvider
  is wired (design/details/control-plane/12-auth.md, 03-provider-seams.md).
- anything else      — treated as a literal (back-compat; discouraged).

See design/details/control-plane/12-auth.md §6.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_CLOUD_SOURCES = {
    "hashicorp-vault",
    "aws-secrets-manager",
    "gcp-secret-manager",
    "azure-key-vault",
    "plugin",
}


def _read_file(path: str) -> str | None:
    try:
        return Path(path).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _resolve_credentials_entry(name: str, credentials: dict[str, Any]) -> str | None:
    """Resolve a workspace ``credentials`` entry to a secret by its source."""
    entry = credentials.get(name)
    if not isinstance(entry, dict):
        raise ValueError(f"credentials entry '{name}' not found in workspace credentials")
    source = entry.get("source")
    config = entry.get("config") or {}
    if source == "env":
        return os.environ.get(config.get("env", ""))
    if source == "file":
        return _read_file(config.get("path", ""))
    if source in _CLOUD_SOURCES:
        raise NotImplementedError(
            f"credentials source '{source}' needs a SecretsProvider, which is not yet wired. "
            "Use key_ref 'env:VAR' or 'file:/path' (e.g. a Vault-agent-rendered file)."
        )
    raise ValueError(f"credentials entry '{name}' has unknown source '{source}'")


def resolve_secret_ref(ref: str, credentials: dict[str, Any] | None = None) -> str | None:
    """Resolve a key_ref to its secret string, or None if unresolvable. Raises only on a
    misconfigured/unsupported credentials reference (fail-loud)."""
    if ref.startswith("env:"):
        return os.environ.get(ref[4:])
    if ref.startswith("file:"):
        return _read_file(ref[5:])
    if ref.startswith("credentials:"):
        return _resolve_credentials_entry(ref[len("credentials:") :], credentials or {})
    return ref  # literal (discouraged)
