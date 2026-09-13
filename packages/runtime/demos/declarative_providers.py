"""Demo: a model provider is a YAML file (design/details/declarative-model-providers.md).

Three scenes, no network, no keys:

1. A workspace points SwarmKit at an OpenAI-compatible server it has never heard of — six lines
   of YAML, no Python — and it registers.
2. The provider inherits from a bundled one and overrides only what differs.
3. The ceiling refuses loudly: a YAML declaring ``requires: code`` stops the listing with its
   file name and the reason, rather than half-working.

Run: ``just demo-providers``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from swarmkit_runtime._workspace_runtime import register_available_providers
from swarmkit_runtime.cli import app
from swarmkit_runtime.model_providers import ProviderRegistry
from typer.testing import CliRunner

LLAMA_SERVER = """apiVersion: swarmkit/v1
kind: ModelProvider
metadata: {id: gpu-box, name: GPU box, description: llama-server on the GPU box, OpenAI-compatible.}
spec:
  extends: openai-compatible
  base_url: ${GPU_BOX_URL:-http://gpu-box:8081/v1}
  models: {accept_any: true}
provenance: {authored_by: human, version: 1.0.0}
"""

CHILD = """apiVersion: swarmkit/v1
kind: ModelProvider
metadata: {id: gpu-box-vision, name: GPU box (vision), description: the same server, tools off.}
spec:
  extends: gpu-box
  capabilities: {tools: false}
provenance: {authored_by: human, version: 1.0.0}
"""

VERTEX = """apiVersion: swarmkit/v1
kind: ModelProvider
metadata: {id: vertex, name: Vertex AI, description: needs service-account OAuth, past the DSL.}
spec:
  extends: google
  requires: code
provenance: {authored_by: human, version: 1.0.0}
"""


def _run(*args: str) -> str:
    result = CliRunner().invoke(app, list(args))
    return result.output.rstrip()


def main() -> None:
    os.environ.pop("GROQ_API_KEY", None)
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        providers = ws / "providers"
        providers.mkdir()

        print("── 1. a provider SwarmKit has never heard of, as a file ──")
        (providers / "gpu-box.yaml").write_text(LLAMA_SERVER)
        print(LLAMA_SERVER)
        registry = ProviderRegistry()
        register_available_providers(registry, ws)
        print(f"registered: {', '.join(registry.provider_ids)}")
        assert "gpu-box" in registry.provider_ids
        print()
        print("$ swarmkit providers show gpu-box")
        print(_run("providers", "show", "gpu-box", str(ws)))
        print()

        print("── 2. inherit from it, override one thing ──")
        (providers / "gpu-box-vision.yaml").write_text(CHILD)
        print(CHILD)
        print("$ swarmkit providers show gpu-box-vision")
        print(_run("providers", "show", "gpu-box-vision", str(ws)))
        print()

        print("── 3. the ceiling refuses loudly ──")
        (providers / "vertex.yaml").write_text(VERTEX)
        print("$ swarmkit providers list")
        print(_run("providers", "list", str(ws)))
        print()

        (providers / "vertex.yaml").unlink()
        print("── and the listing, with the ceiling removed ──")
        print("$ swarmkit providers list")
        print(_run("providers", "list", str(ws)))


if __name__ == "__main__":
    main()
