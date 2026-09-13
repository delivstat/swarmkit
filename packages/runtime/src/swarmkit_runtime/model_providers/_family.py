"""What every wire-format family shares once it is parameterised by a provider YAML.

A family is code: it knows how to speak one API. A provider is data: it says where, with what
key, for which models, with which capabilities. `FamilyBase` is the seam between the two — the
constructor parameters every family accepts, and the two behaviours that follow from them:

* ``supports(model)`` — the catalogue, from ``models.pattern`` / ``models.accept_any``;
* ``_check(request)`` — a request that uses a capability the YAML switched OFF is refused
  before it reaches the wire. Sending tools to a runtime that half-parses them produces an agent
  that loops; the YAML said not to, so the family does not.

Called with no parameters, every family behaves exactly as it did before there were YAMLs.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from ._types import CompletionRequest


class CapabilityError(ValueError):
    """The request needs something the provider declared it does not do."""


class FamilyBase:
    provider_id: str
    enforces_response_schema: bool

    def _configure(
        self,
        *,
        provider_id: str | None,
        model_pattern: str | None,
        accept_any_model: bool,
        capabilities: Mapping[str, bool] | None,
        default_pattern: str | None,
        default_accept_any: bool,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        if provider_id:
            self.provider_id = provider_id
        # Unparameterised: the family's own catalogue. Parameterised: the YAML's, with
        # ``accept_any`` (aggregators, local runtimes) beating a pattern.
        if model_pattern is None and not accept_any_model:
            model_pattern, accept_any_model = default_pattern, default_accept_any
        self._model_re = re.compile(model_pattern) if model_pattern else None
        self._accept_any = accept_any_model
        self.capabilities: dict[str, bool] = dict(capabilities or {})
        if "structured_output" in self.capabilities:
            # An instance attribute shadows the class declaration — the YAML narrowed it.
            self.enforces_response_schema = bool(
                self.capabilities["structured_output"] and type(self).enforces_response_schema
            )
        self.extra_headers: dict[str, str] = dict(headers or {})

    def supports(self, model: str) -> bool:
        if self._accept_any:
            return True
        return bool(self._model_re and self._model_re.match(model))

    def _lacks(self, capability: str) -> bool:
        return self.capabilities.get(capability, True) is False

    def _check(self, request: CompletionRequest) -> None:
        if request.tools and self._lacks("tools"):
            raise CapabilityError(
                f"provider '{self.provider_id}' declares capabilities.tools: false, and this "
                f"request carries {len(request.tools)} tool(s)"
            )
        if self._lacks("images") and _has_images(request):
            raise CapabilityError(
                f"provider '{self.provider_id}' declares capabilities.images: false, and this "
                "request carries an image block"
            )

    def _check_stream(self) -> None:
        if self._lacks("streaming"):
            raise CapabilityError(
                f"provider '{self.provider_id}' declares capabilities.streaming: false"
            )

    @property
    def _structured_output(self) -> bool:
        """Whether the request's ``response_format`` goes to the wire. When the YAML said the
        runtime cannot honour one it is withheld, and the schema stays in the prompt
        (``enforces_response_schema`` is already False, so the compiler pasted it there)."""
        return not self._lacks("structured_output")


def _has_images(request: CompletionRequest) -> bool:
    for msg in request.messages:
        if isinstance(msg.content, str):
            continue
        if any(b.type == "image" for b in msg.content):
            return True
    return False
