#!/usr/bin/env python3
"""Demo: `think` set on an agent reaches Ollama where Ollama actually reads it.

`model.options` is the only provider-native passthrough an archetype has, and `think` has always
been carried through it — accepted by the schema, dropped by the non-Ollama adapters as
Ollama-specific, and then folded into Ollama's `options` object, which is not where Ollama looks for
it. The setting was honoured by nobody.

Run: uv run python packages/runtime/demos/ollama_think_option.py
"""

from swarmkit_runtime.model_providers._ollama import _to_ollama_payload
from swarmkit_runtime.model_providers._types import CompletionRequest, Message


def payload(model: str, options: dict | None = None) -> dict:
    return _to_ollama_payload(
        CompletionRequest(
            model=model,
            messages=(Message(role="user", content="is anyone at the main door"),),
            max_tokens=256,
            options=options,
        )
    )


def show(title: str, p: dict) -> None:
    print(f"\n  {title}")
    print(f"    payload.think    = {p.get('think', '<absent>')}")
    print(f"    payload.options  = {p.get('options', {})}")


print("An agent that asks for no reasoning:\n    model.options: {think: false}")
show("as sent to Ollama", payload("qwen3.5:2b", {"think": False}))
print("\n    `think` sits at the payload root, which is the only place Ollama reads it.")

show("alongside other options", payload("qwen3.5:2b", {"think": False, "num_ctx": 8192}))
print("\n    Unrelated options still fold into `options`; only the root-level keys are lifted out.")

show("a Gemma agent, unconfigured", payload("gemma4:e2b"))
print("    Gemma still defaults to no thinking — its <think> blocks break Ollama's tool parser.")

show("a Gemma agent that asks for reasoning", payload("gemma4:e2b", {"think": True}))
print("    ...but the author can override it. A good default, not a law.")

show("any other model, unconfigured", payload("qwen3.5:2b"))
print("    Nothing is assumed for a model nobody configured.")

print(
    "\n  Why it matters: reasoning is charged against the same budget as the answer. Measured on\n"
    "  qwen3.5:0.8b at num_predict=256, all 256 tokens went inside the thinking block and the\n"
    "  reply came back EMPTY (done_reason=length). A model configured that way looks incapable\n"
    "  rather than misconfigured.\n"
)
