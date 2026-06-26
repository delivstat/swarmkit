# Minder Model-Runtime Options — Design Note

## Goal

Record *how* Minder runs LLM/VLM inference on its hardware, and why — so the
runtime stance is explicit and candidate runtimes get evaluated against the real
constraint instead of the headline claim. This note is the place to park
"should we use X to run bigger models?" questions.

## The constraint (read this first)

Minder runs on a **small always-on box: ~4 GB, CPU-first** (the GPU thrashes on
the VLM, so vision runs on CPU — see [vision-architecture.md](vision-architecture.md)).
The binding constraint is **usable latency on that box**, *not* "a model is too
big to fit." Two paths are latency-sensitive:

- **Interactive** — the conversation router parses each request into a Plan and
  the backend executes deterministically ([conversation-router.md](conversation-router.md)).
  Telegram/Discord replies need to feel immediate.
- **Per-minute cron** — the vision poller and monitoring-rule tick run on a
  one-minute cadence; any single inference that takes longer than the tick
  breaks the loop.

## The stance: small quantized models on Ollama + deterministic code

Minder deliberately uses **small quantized models** (e.g. `llama3.2:3b` for the
poller's reasoning, qwen-vl 2–3B for vision) served by **Ollama**, with
**deterministic code doing the actual work** — the LLM parses language into a
structured Plan and code executes it ([conversation-router.md](conversation-router.md);
the broader principle is "LLM for language, code for doing"). This is the right
trade for the hardware: models that fit *and* run fast enough, with reliability
coming from code rather than from a bigger model.

Anything that proposes a different runtime has to beat *that* — on this box, on
these two latency-bound paths — not just fit in memory.

## Evaluated and rejected for the live box

### AirLLM (`lyogavin/airllm`)

**What it is.** A layer-streaming inference trick (not a compression library).
Model weights stay **on disk**; at inference it loads **one transformer layer at
a time** into memory, computes, unloads, and repeats through the whole stack for
*every* forward pass. Peak memory stays ~4–6 GB even for a 70B model (claims:
70B on 4 GB, 405B on 8 GB) with no quantization required. Optional 4/8-bit
block-wise quant for a claimed ~3×. CPU inference since v2.10.1. `pip install
airllm`; pulls weights from HuggingFace Hub. <https://github.com/lyogavin/airllm>

**Why rejected here.** The memory win is paid for in **disk I/O per token**:
every generated token streams the entire model off disk. Throughput is therefore
disk-bound — seconds-to-minutes per token on anything but fast NVMe (no published
tokens/sec; no CPU latency figures at all). On Minder's box that means:

- **Unusable for the interactive router** — minutes/token is not a reply.
- **Breaks the per-minute cron** — a single inference can exceed the tick.
- **Flash wear** — streaming the full model every token hammers SD/eMMC on an
  always-on appliance.

AirLLM optimizes the *opposite* axis from Minder's constraint: it lets a model
far larger than your memory run *at all*, by accepting terrible speed. For
on-device, latency-bound work, a quantized GGUF on Ollama/llama.cpp beats it on
both speed and practicality.

**Where it could still fit (off-box, not this deliverable).** An **offline,
off-box batch** job where 70B-class quality matters and wall-clock does not —
e.g. on a workstation generating eval/training data, or a nightly deep-analysis
pass over recorded events. Never on the live Minder box, never on a latency-bound
path.

## Decision rule for future runtimes

A candidate runtime is in-scope for the **live box** only if it keeps both
latency-bound paths usable (sub-second-ish interactive, sub-tick cron) within
~4 GB on CPU. "Runs a bigger model in less memory" is not sufficient — measure
tokens/sec on the target hardware first. Big-model quality belongs in **offline,
off-box** jobs.
