# The portal

`pip install "swarmkit-runtime[ui]"` and `swarmkit serve` host this at the same origin as the API.
There is no separate deployment.

Every image below is **generated from the version in the tree** by `scripts/media/capture.mjs`
driving a real `swarmkit serve` — so they go stale when someone forgets to run the script, rather
than silently. The workspace is [`examples/showcase/`](https://github.com/delivstat/swarmkit/tree/main/examples/showcase),
which runs with no API keys and no network.

<video controls preload="none" poster="img/portal/canvas.png" style="width:100%;border-radius:8px">
  <source src="img/portal/portal-tour.webm" type="video/webm">
</video>

## The swarm as a graph

The topology is YAML. The canvas renders it — nothing is drawn by hand, and the `GATED` badge is
the funnel the coordinator must pass.

![The topology canvas](img/portal/canvas.png)

The same topology as structure, with each agent's model, skills and children read back from the
file:

![The composer](img/portal/composer.png)

## A run waiting on a person

`release-manager` must confer `release:approve`. No agent can hold that scope, whatever its prompt
says — the policy engine enforces it (design §8.7).

![The gates inbox](img/portal/gates.png)

## What ran, and what it cost

![Jobs](img/portal/jobs.png)

## The append-only trail

No update or delete path is exposed to an agent, ever (design §8.3).

![The audit log](img/portal/audit.png)

## What the workspace can reach

Servers, event sinks and the credentials they use — including whether each credential **resolves
right now**, which is the failure that is otherwise invisible until a run gets an auth error.

![Connections](img/portal/connections.png)

## The catalogue

![Skills](img/portal/skills.png)

![Archetypes](img/portal/archetypes.png)

## Regenerating these

```bash
swarmkit serve examples/showcase/workspace --port 8140 &
curl -X POST localhost:8140/run/release -d '{"input":"…"}'   # so the gate screens have content
node scripts/media/capture.mjs --serve http://127.0.0.1:8140 --out docs/site/img/portal
```

The script refuses to photograph a 404 — the first run of it saved the portal's own not-found page
as `connections.png`, because the serve was hosting a build from before that page existed.
