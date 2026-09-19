# Agentic runtime demo

A ~30-second terminal demo of what the SwarmKit **runtime** does around a *single* model call.
SwarmKit started as a way to run multi-agent swarms; it became an agentic runtime — the layer that
owns the model-call loop. The point of this demo is that the interesting part is not "many agents,"
it is what the runtime does with each call: run it, record it to an append-only audit log, and
account for its tokens and cost. One agent shows that as clearly as a swarm.

It runs entirely on the built-in **mock** provider — no API keys, no network — so anyone can
reproduce it.

## What it shows

1. **An agent is data.** `topologies/assistant.yaml` is the whole agent: which model answers and how
   it is told to behave. No Python.
2. **The runtime owns the call.** `swarmkit run` executes it — the runtime, not your app, makes the
   model call, under governance.
3. **Every call is recorded.** `swarmkit trace <run-id>` shows the call graph, tokens by agent and
   by model, and cost.
4. **The record is an append-only audit.** `swarmkit logs --format markdown` is a compliance-ready
   report of the same run, not a log line.

## Run it

```bash
bash examples/agentic-runtime/demo.sh
```

## Record it (asciinema)

```bash
uvx asciinema rec -c "bash examples/agentic-runtime/demo.sh" demo.cast
asciinema upload demo.cast        # prints a shareable URL
```

`DEMO_PAUSE=0.6 bash …` speeds up the pacing; the default (1.4s) is tuned for a watchable recording.
