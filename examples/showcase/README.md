# Showcase — SwarmKit in one run

The demo to put in front of someone evaluating SwarmKit. Nine steps, about thirty seconds, on the
version you actually have installed.

```bash
just demo-showcase
```

**No API keys and no network.** The mock model provider answers deterministically, so it runs on a
laptop with the wifi off and prints the same thing every time — which is what you want in front of
an audience.

## What it shows, and why each step is there

| | claim | how it is proved |
| --- | --- | --- |
| 1 | the swarm is **data**, not code | reads the topology back from the API |
| 2 | an application drives SwarmKit | one `POST /run/release` |
| 3 | a run **parks** on a human | status becomes `deferred` — nothing resident |
| 4 | the runtime says what happened | `GET /events` carries `funnel.gate_opened` |
| 5 | the gate names a **role and scope** | `release-manager (release:approve)` |
| 6 | a person decides, anywhere | `POST /review/{id}/resolve` |
| 7 | the run continues on its own | no resume call is made |
| 8 | who decided what | resolver, comment and artifact, durably |
| 9 | nothing declared is left unwired | `GET /workspace/reachability` |

Step 5 is the one worth pausing on for a governance-minded audience: `release:approve` is conferred
by a **role**, and no agent can hold it whatever its prompt says.

## What it deliberately does not claim

The workspace runs `governance: mock`, whose `record_event` does not persist. So the approval is
shown from the **review record** — durable, with resolver and comment — rather than from the audit
stream, where a real provider (AGT) would also write it. A demo that asserted an audit line it
could not show is exactly what a careful prospect checks.

The event trail also de-duplicates: a gate is announced again when a resuming run re-enters the
gated node, which a consumer has to handle and a demo should not pretend away.

## The workspace

```
workspace/
├── workspace.yaml      mock governance, one stdout event sink
├── topologies/         release: coordinator -> risk-analyst, gated
├── archetypes/         the two agents, with their scopes
├── funnels/            release-approval — the human gate
└── roles/              who may confer release:approve
```

Four files and about sixty lines. That is the whole swarm — which is itself part of the pitch.

## Against a serve you already have

```bash
swarmkit serve examples/showcase/workspace --port 8000 &
python examples/showcase/demo.py --serve http://127.0.0.1:8000
```

## Then show the portal

`swarmkit serve` hosts the workspace portal at the same origin. Worth opening after the terminal
run: the **topology canvas** renders the same YAML the demo just read back, and **Gates** shows the
approval that was resolved.
