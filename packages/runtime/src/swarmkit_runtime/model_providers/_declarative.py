"""Declarative model providers (design/details/declarative-model-providers.md).

A provider is DATA — a base URL, an auth shape, a model catalogue, capabilities and request quirks
— layered over a wire-format FAMILY that stays code. There are four families and they are the only
Python here: ``openai-compatible``, ``anthropic``, ``google``, ``ollama``. Everything a provider
YAML says parameterises one of them; a YAML never imports anything, so invariant 4 (only
``model_providers/`` touches vendor SDKs) is untouched.

Mirrors ``executors/_declarative.py``: a bundled library ships with the runtime
(``model_providers/providers/*.yaml``) and a workspace's own ``providers/`` directory loads over it
and may override a bundled id.

Three things are refused at load rather than tolerated, because each is a way for a silent wrong
answer to reach an audit log:

* an ``extends`` chain that never reaches a family, or revisits an id;
* a capability set ``true`` that the family does not implement (capabilities may only NARROW);
* ``requires: code`` — the artifact says itself it is past the DSL's ceiling.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from swarmkit_runtime.resolver._env_config import _resolve_env_vars

from ._registry import ModelProviderProtocol

#: The bundled provider library.
_BUNDLED_PROVIDERS_DIR = Path(__file__).resolve().parent / "providers"

#: Family id → (module, class). The wire formats. Adding one here is adding code, and that is the
#: point: it is the line between what a YAML may say and what needs a release.
FAMILIES: Mapping[str, tuple[str, str]] = {
    "openai-compatible": ("._openai", "OpenAIModelProvider"),
    "anthropic": ("._anthropic", "AnthropicModelProvider"),
    "google": ("._google", "GoogleModelProvider"),
    "ollama": ("._ollama", "OllamaModelProvider"),
}

CAPABILITY_KEYS = ("images", "tools", "streaming", "structured_output")

#: What each family actually implements. A YAML may switch any of these OFF for a runtime that
#: half-supports it; it may never switch one ON. ``structured_output`` follows each family's
#: ``enforces_response_schema`` — Anthropic does not read ``response_format`` at all.
FAMILY_CAPABILITIES: Mapping[str, Mapping[str, bool]] = {
    "openai-compatible": {
        "images": True,
        "tools": True,
        "streaming": True,
        "structured_output": True,
    },
    "anthropic": {"images": True, "tools": True, "streaming": True, "structured_output": False},
    "google": {"images": True, "tools": True, "streaming": True, "structured_output": True},
    "ollama": {"images": True, "tools": True, "streaming": True, "structured_output": True},
}

#: Which spec fields each family honours beyond the universal ones (``base_url``,
#: ``auth.api_key_env``, ``models``, ``capabilities``, ``headers``). A YAML setting a field its
#: family ignores is refused, not dropped: a declared ``extra_body`` that never reaches the wire
#: is exactly the half-working provider this design exists to prevent.
FAMILY_FIELDS: Mapping[str, frozenset[str]] = {
    "openai-compatible": frozenset({"extra_body", "auth.header", "auth.scheme"}),
    "ollama": frozenset({"extra_body", "lift_to_root", "auth.header", "auth.scheme"}),
    "anthropic": frozenset(),
    "google": frozenset(),
}

#: Family defaults for the fields a YAML may leave unsaid. These are what the families did before
#: they were parameterised, so a YAML that says nothing but ``extends`` is behaviour-identical to
#: the class it names.
FAMILY_DEFAULTS: Mapping[str, Mapping[str, Any]] = {
    "openai-compatible": {"models": {"pattern": r"^(gpt-|o1-|o3-|o4-)"}},
    "anthropic": {"models": {"pattern": r"^claude-"}},
    "google": {"models": {"pattern": r"^gemini-"}},
    "ollama": {
        "base_url": "http://localhost:11434",
        "models": {"accept_any": True},
        "options": {"lift_to_root": ["think", "keep_alive"]},
    },
}


class ProviderSpecError(ValueError):
    """A provider artifact that cannot be loaded, with the reason."""


@dataclass(frozen=True)
class AuthSpec:
    """A static key in a header — the one auth shape every existing provider uses."""

    api_key_env: str | None = None
    header: str = "Authorization"
    scheme: str = "Bearer"

    @property
    def is_bearer(self) -> bool:
        return self.header.lower() == "authorization" and self.scheme == "Bearer"

    def resolve(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    @property
    def ready(self) -> bool:
        """No auth at all (a local runtime), or the key is in the environment."""
        return self.api_key_env is None or bool(os.environ.get(self.api_key_env))


@dataclass(frozen=True)
class ProviderSpec:
    """One provider, as declared — before its chain is resolved."""

    id: str
    name: str
    description: str
    extends: str
    base_url: str | None = None
    auth: Mapping[str, Any] = field(default_factory=dict)
    models: Mapping[str, Any] = field(default_factory=dict)
    capabilities: Mapping[str, bool] = field(default_factory=dict)
    lift_to_root: tuple[str, ...] | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    requires: str | None = None
    version: str = "0.0.0"
    source: str = "<memory>"


@dataclass(frozen=True)
class ResolvedProvider:
    """A provider with its chain walked to a family and every hop merged. What ``build`` reads."""

    id: str
    family: str
    chain: tuple[str, ...]
    base_url: str | None
    auth: AuthSpec
    model_pattern: str | None
    accept_any_model: bool
    capabilities: Mapping[str, bool]
    lift_to_root: tuple[str, ...]
    headers: Mapping[str, str]
    extra_body: Mapping[str, Any]

    @property
    def ready(self) -> bool:
        return self.auth.ready


# ---- parse -------------------------------------------------------------------------------------


def parse_provider_spec(raw: Mapping[str, Any], *, source: str = "<memory>") -> ProviderSpec:
    """Shape a schema-validated ``ModelProvider`` artifact into a :class:`ProviderSpec`.

    Shape only — the schema has already said the artifact is well-formed. The one check here is
    the one the schema cannot express: ``requires: code`` is a valid artifact and an unloadable
    provider, and the refusal must name the file.
    """
    spec = raw.get("spec") or {}
    meta = raw.get("metadata") or {}
    pid = str(meta.get("id", ""))
    if spec.get("requires") == "code":
        raise ProviderSpecError(
            f"{source}: provider '{pid}' declares requires: code — this provider cannot be "
            "expressed declaratively. Implement it as a family (see "
            "design/details/declarative-model-providers.md)."
        )
    options = spec.get("options") or {}
    lift = options.get("lift_to_root")
    caps_raw = spec.get("capabilities") or {}
    return ProviderSpec(
        id=pid,
        name=str(meta.get("name", pid)),
        description=str(meta.get("description", "")),
        extends=str(spec["extends"]),
        base_url=spec.get("base_url"),
        auth=dict(spec.get("auth") or {}),
        models=dict(spec.get("models") or {}),
        capabilities={k: bool(v) for k, v in caps_raw.items() if v is not None},
        lift_to_root=tuple(str(k) for k in lift) if lift is not None else None,
        headers={str(k): str(v) for k, v in (spec.get("headers") or {}).items()},
        extra_body=dict(spec.get("extra_body") or {}),
        requires=spec.get("requires"),
        version=str((raw.get("provenance") or {}).get("version", "0.0.0")),
        source=source,
    )


# ---- resolve -----------------------------------------------------------------------------------


def _walk(pid: str, specs: Mapping[str, ProviderSpec]) -> tuple[str, list[ProviderSpec]]:
    """Follow ``extends`` from ``pid`` to a family. Returns the family and the hops, leaf first.

    Families and providers share one namespace, and four bundled providers carry their family's
    name (``anthropic`` the provider extends ``anthropic`` the family). So the FIRST hop is always
    the provider ``pid`` names, and every ``extends`` after it resolves to a family before a
    provider — ``extends: ollama`` is the wire format, never the bundled ``ollama.yaml``.
    """
    spec = specs.get(pid)
    if spec is None:
        raise ProviderSpecError(f"provider '{pid}' is not declared")
    chain = [pid]
    hops = [spec]
    cur = spec.extends
    while cur not in FAMILIES:
        if cur in chain:
            raise ProviderSpecError(
                f"provider '{pid}': extends chain is a cycle: {' -> '.join([*chain, cur])}"
            )
        spec = specs.get(cur)
        if spec is None:
            raise ProviderSpecError(
                f"provider '{pid}': extends chain never reaches a family: "
                f"{' -> '.join([*chain, cur])} — '{cur}' is neither a provider nor one of "
                f"{', '.join(sorted(FAMILIES))}"
            )
        chain.append(cur)
        hops.append(spec)
        cur = spec.extends
    return cur, hops


def _merge(family: str, hops: list[ProviderSpec]) -> dict[str, Any]:
    """Family defaults first, then each hop from the family outward, so the leaf wins.
    ``auth``, ``models``, ``capabilities``, ``headers`` and ``extra_body`` merge by key; scalars
    and ``lift_to_root`` replace."""
    defaults = FAMILY_DEFAULTS.get(family, {})
    lift_default = (defaults.get("options") or {}).get("lift_to_root")
    merged: dict[str, Any] = {
        "base_url": defaults.get("base_url"),
        "auth": {},
        "models": dict(defaults.get("models") or {}),
        "capabilities": {},
        "lift_to_root": tuple(lift_default) if lift_default else (),
        "headers": {},
        "extra_body": {},
    }
    for hop in reversed(hops):
        if hop.base_url is not None:
            merged["base_url"] = hop.base_url
        merged["auth"].update(hop.auth)
        merged["models"].update(hop.models)
        merged["capabilities"].update(hop.capabilities)
        if hop.lift_to_root is not None:
            merged["lift_to_root"] = hop.lift_to_root
        merged["headers"].update(hop.headers)
        merged["extra_body"].update(hop.extra_body)
    return merged


def _check_honoured(pid: str, family: str, hops: list[ProviderSpec]) -> None:
    """A field the family would ignore is refused, not dropped."""
    declared: set[str] = set()
    for hop in hops:
        if hop.extra_body:
            declared.add("extra_body")
        if hop.lift_to_root is not None:
            declared.add("lift_to_root")
        declared.update(f"auth.{key}" for key in ("header", "scheme") if key in hop.auth)
    ignored = sorted(declared - FAMILY_FIELDS[family])
    if ignored:
        raise ProviderSpecError(
            f"provider '{pid}': {', '.join(ignored)} would be ignored by family '{family}', "
            "which owns its wire format. Remove the field, or extend a family that honours it."
        )


def _check_capabilities(pid: str, family: str, declared: Mapping[str, bool]) -> dict[str, bool]:
    """The family's capabilities, narrowed by the YAML. A ``true`` the family lacks is refused."""
    family_caps = FAMILY_CAPABILITIES[family]
    widened = [k for k, v in declared.items() if v and not family_caps.get(k, False)]
    if widened:
        raise ProviderSpecError(
            f"provider '{pid}': capabilities {', '.join(sorted(widened))} are not implemented by "
            f"family '{family}'. A YAML may narrow a family's capabilities, never widen them."
        )
    caps = {k: bool(family_caps.get(k, False)) for k in CAPABILITY_KEYS}
    caps.update({k: v for k, v in declared.items() if k in CAPABILITY_KEYS})
    return caps


def resolve_chain(pid: str, specs: Mapping[str, ProviderSpec]) -> ResolvedProvider:
    """Walk ``extends`` from ``pid`` to a family, merging each hop's fields over its parent's.

    A chain that never reaches a family, or that revisits an id, is refused with the chain
    printed — a provider that cannot be read top to bottom is one nobody can review. So is a
    field the family would ignore, and a capability the family does not have.
    """
    family, hops = _walk(pid, specs)
    _check_honoured(pid, family, hops)
    merged = _merge(family, hops)
    caps = _check_capabilities(pid, family, merged["capabilities"])

    auth_raw = merged["auth"]
    auth = AuthSpec(
        api_key_env=auth_raw.get("api_key_env"),
        header=str(auth_raw.get("header") or "Authorization"),
        scheme=str(auth_raw["scheme"]) if "scheme" in auth_raw else "Bearer",
    )
    base_url = merged["base_url"]
    if base_url is not None:
        base_url = _resolve_env_vars(str(base_url))
    pattern = merged["models"].get("pattern")
    if pattern is not None:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ProviderSpecError(
                f"provider '{pid}': models.pattern {pattern!r} is not a regex: {exc}"
            ) from exc
    return ResolvedProvider(
        id=pid,
        family=family,
        chain=(*(h.id for h in hops), family),
        base_url=base_url,
        auth=auth,
        model_pattern=pattern,
        accept_any_model=bool(merged["models"].get("accept_any", False)),
        capabilities=caps,
        lift_to_root=tuple(merged["lift_to_root"]),
        headers=dict(merged["headers"]),
        extra_body=dict(merged["extra_body"]),
    )


# ---- load --------------------------------------------------------------------------------------


def _load_dir(directory: Path) -> dict[str, ProviderSpec]:
    specs: dict[str, ProviderSpec] = {}
    if not directory.is_dir():
        return specs
    for path in sorted(directory.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(raw, Mapping) and raw.get("kind") == "ModelProvider":
            spec = parse_provider_spec(raw, source=str(path))
            if spec.id in specs:
                raise ProviderSpecError(
                    f"{path}: provider id '{spec.id}' is already declared by "
                    f"{specs[spec.id].source}"
                )
            specs[spec.id] = spec
    return specs


def load_provider_specs(workspace_root: Path | str | None = None) -> dict[str, ProviderSpec]:
    """Discover declarative providers. The bundled library loads first; a workspace's own
    ``providers/`` directory loads second and may override a bundled id."""
    specs = _load_dir(_BUNDLED_PROVIDERS_DIR)
    if workspace_root is not None:
        specs.update(_load_dir(Path(workspace_root) / "providers"))
    return specs


def resolve_all(specs: Mapping[str, ProviderSpec]) -> dict[str, ResolvedProvider]:
    return {pid: resolve_chain(pid, specs) for pid in specs}


# ---- build -------------------------------------------------------------------------------------


def family_class(family: str) -> type[Any]:
    """The Python class behind a family id. Imports lazily — SDKs are optional extras."""
    import importlib  # noqa: PLC0415

    module_path, class_name = FAMILIES[family]
    mod = importlib.import_module(module_path, __package__)
    cls: type[Any] = getattr(mod, class_name)
    return cls


def build_provider(resolved: ResolvedProvider) -> ModelProviderProtocol:
    """Instantiate the family with the resolved parameters. Raises ``ImportError`` when the
    family's SDK is not installed — the caller decides whether that is fatal — and
    :class:`ProviderSpecError` when the provider needs a key that is not set, so the failure
    names the env var rather than surfacing as the SDK's own credentials error."""
    if not resolved.ready:
        raise ProviderSpecError(f"provider '{resolved.id}' needs {resolved.auth.api_key_env} set")
    cls = family_class(resolved.family)
    provider: ModelProviderProtocol = cls(
        api_key=resolved.auth.resolve(),
        provider_id=resolved.id,
        base_url=resolved.base_url,
        auth=resolved.auth,
        headers=resolved.headers,
        extra_body=resolved.extra_body,
        lift_to_root=resolved.lift_to_root,
        model_pattern=resolved.model_pattern,
        accept_any_model=resolved.accept_any_model,
        capabilities=resolved.capabilities,
    )
    return provider


def enforces_response_schema(resolved: ResolvedProvider) -> bool:
    """Whether a resolved provider constrains decoding to the response schema: the family's
    declaration, narrowed by the YAML's ``capabilities.structured_output``."""
    return bool(resolved.capabilities.get("structured_output", False))
