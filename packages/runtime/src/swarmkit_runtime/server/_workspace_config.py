"""Reading and writing the infrastructure half of `workspace.yaml` from the portal.

The portal has always edited *artifacts* — topologies, skills, archetypes, funnels, contracts — and
never *infrastructure*. So `mcp_servers`, `credentials` and `channels` have only ever been settable
by hand-editing YAML and exporting environment variables, which is the whole of why connecting a
Telegram bot is a four-step document rather than a form. `docs/notes/schema-change-discipline.md`
already says a user-authored field settable only by hand-editing YAML is an incomplete schema
change; this is the service layer that lets the portal keep that promise.

Two properties shape the implementation:

**The file is hand-edited and committed.** A write from the portal must not reformat somebody's
workspace or drop the comment explaining why a server is `cautious`. That is why this uses
round-trip YAML rather than parse-and-re-emit — the diff after saving a form should be the field
that changed, not the whole file.

**Secrets never travel back.** A credential is returned as its shape — id, source, whether it
resolves — never its value. The browser is shown *configured*, and a token that reached a browser
would be a token in someone's devtools, their history, and any extension they have installed.

`design/details/mcp-oauth.md` decides what a connection binds to: the `mcp_servers` entry, not the
skill, archetype or topology.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from swarmkit_runtime.server._services import NotFoundError, ServiceError

#: Blocks the portal may edit. Deliberately not `governance`, `storage` or `identity`: those change
#: how the runtime is *governed*, and a form is the wrong instrument for a decision that wants a
#: review. Artifacts have their own CRUD; this is the infrastructure beneath them.
EDITABLE = ("credentials", "mcp_servers", "channels")

#: Sections whose entries are keyed by an `id` field inside a list, rather than by mapping key.
_LIST_KEYED = {"mcp_servers"}


class ConfigError(ServiceError):
    """A write that would produce a workspace that does not load."""

    status = 400


@dataclass
class WorkspaceConfigService:
    """Read and write `workspace.yaml`'s infrastructure blocks, comments intact."""

    workspace_path: Path

    def __post_init__(self) -> None:
        self._yaml = YAML()
        self._yaml.preserve_quotes = True
        # Match the house style in reference/workspace.yaml rather than ruamel's defaults, so a
        # portal write and a hand edit produce the same shape.
        self._yaml.indent(mapping=2, sequence=4, offset=2)

    @property
    def _file(self) -> Path:
        return self.workspace_path / "workspace.yaml"

    # ---- reading -------------------------------------------------------------------------

    def _load(self) -> Any:
        if not self._file.exists():
            raise NotFoundError(f"No workspace.yaml under {self.workspace_path}")
        return self._yaml.load(self._file.read_text()) or {}

    def read(self) -> dict[str, Any]:
        """Every editable block, with credential values replaced by whether they resolve."""
        doc = self._load()
        credentials = doc.get("credentials") or {}
        return {
            "credentials": [
                {
                    "id": cid,
                    "source": (entry or {}).get("source", ""),
                    "config": _redact_config(entry or {}),
                    "resolves": self._resolves(cid, credentials),
                }
                for cid, entry in credentials.items()
            ],
            "mcp_servers": [dict(s) for s in (doc.get("mcp_servers") or [])],
            "channels": [
                {"id": cid, **dict(entry or {})}
                for cid, entry in (doc.get("channels") or {}).items()
            ],
        }

    def _resolves(self, credential_id: str, credentials: dict[str, Any]) -> bool:
        """Whether the credential produces a value here and now.

        This is the answer a setup screen actually needs. `source: env` pointing at a variable
        nobody exported looks identical to a working credential in every other view, and the
        failure surfaces much later as a platform auth error.
        """
        from swarmkit_runtime.mcp._credentials import substitute  # noqa: PLC0415

        try:
            return bool(substitute(f"{{credential.{credential_id}}}", credentials))
        except Exception:
            return False

    # ---- writing -------------------------------------------------------------------------

    def upsert(self, section: str, entry_id: str, value: dict[str, Any]) -> dict[str, Any]:
        """Add or replace one entry, then validate. A write that would not load is rolled back."""
        self._check_section(section)
        doc = self._load()
        original = self._file.read_text()

        if section in _LIST_KEYED:
            items = doc.setdefault(section, [])
            payload = {"id": entry_id, **{k: v for k, v in value.items() if k != "id"}}
            for i, item in enumerate(items):
                if item.get("id") == entry_id:
                    items[i] = payload
                    break
            else:
                items.append(payload)
        else:
            doc.setdefault(section, {})[entry_id] = value

        return self._save_or_restore(doc, original, f"{section}/{entry_id}")

    def delete(self, section: str, entry_id: str) -> dict[str, Any]:
        """Remove one entry. Refuses when something still points at it."""
        self._check_section(section)
        doc = self._load()
        original = self._file.read_text()

        if section in _LIST_KEYED:
            items = doc.get(section) or []
            remaining = [i for i in items if i.get("id") != entry_id]
            if len(remaining) == len(items):
                raise NotFoundError(f"{section} '{entry_id}' not found")
            self._refuse_if_referenced(doc, entry_id)
            doc[section] = remaining
        else:
            block = doc.get(section) or {}
            if entry_id not in block:
                raise NotFoundError(f"{section} '{entry_id}' not found")
            self._refuse_if_referenced(doc, entry_id)
            del block[entry_id]

        return self._save_or_restore(doc, original, f"{section}/{entry_id}")

    def _refuse_if_referenced(self, doc: Any, entry_id: str) -> None:
        """A credential still named by a server or channel is not free to delete.

        Deleting it would leave a workspace that resolves until the moment something tries to
        authenticate — the failure would surface in a run, far from the click that caused it.
        """
        users: list[str] = [
            f"mcp_servers/{s.get('id')}"
            for s in (doc.get("mcp_servers") or [])
            if s.get("credentials_ref") == entry_id
        ]
        users += [
            f"channels/{cid}"
            for cid, c in (doc.get("channels") or {}).items()
            if (c or {}).get("credentials_ref") == entry_id
        ]
        if users:
            msg = (
                f"'{entry_id}' is still referenced by {', '.join(users)}. "
                f"Repoint or remove those first."
            )
            raise ConfigError(msg)

    def _check_section(self, section: str) -> None:
        if section not in EDITABLE:
            msg = f"'{section}' is not portal-editable. Editable: {', '.join(EDITABLE)}."
            raise ConfigError(msg)

    def _save_or_restore(self, doc: Any, original: str, what: str) -> dict[str, Any]:
        """Write, validate, and put the old file back if the result does not load.

        Validation happens against the file on disk because that is what the runtime reads. A
        half-written workspace left behind by a rejected form is worse than a rejected form.
        """
        buf = io.StringIO()
        self._yaml.dump(doc, buf)
        self._file.write_text(buf.getvalue())

        from swarmkit_runtime.errors import ResolutionErrors  # noqa: PLC0415
        from swarmkit_runtime.resolver import resolve_workspace  # noqa: PLC0415

        try:
            resolve_workspace(self.workspace_path)
        except ResolutionErrors as exc:
            self._file.write_text(original)
            errors = [{"code": e.code, "message": e.message} for e in exc.errors]
            return {"saved": False, "entry": what, "errors": errors}
        except Exception as exc:
            self._file.write_text(original)
            return {
                "saved": False,
                "entry": what,
                "errors": [{"code": "resolve", "message": str(exc)}],
            }
        return {"saved": True, "entry": what}


def _redact_config(entry: dict[str, Any]) -> dict[str, Any]:
    """A credential's config minus anything that could carry the secret itself.

    `source: env` config names a variable, which is safe and useful to show. A literal value is not
    supposed to exist here at all — but if one does, it must not be the portal that publishes it.
    """
    safe = {"env", "path", "provider", "owner", "vault_path", "secret_id", "key"}
    return {k: v for k, v in (entry.get("config") or {}).items() if k in safe}
