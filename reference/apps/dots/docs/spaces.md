# Spaces — live stack recipe

How to run the dots app's Spaces slice against a real `swarmkit serve` on your
box, and capture the screenshots in `docs/spaces-screenshots/`.

The design is in `design/details/spaces-pattern.md`. The app-side details are
in [`architecture.md`](architecture.md) §6.

---

## 1. Workspace

Any workspace that has:

- `authoring: { expose: true }` on `workspace.yaml` (lets the Author Dot write
  topologies),
- at least two topologies the Space can delegate to.

A minimal demo (used by the capture script) lives in `/tmp/spaces-demo/`:

```yaml
# workspace.yaml
apiVersion: swarmkit/v1
kind: Workspace
name: spaces-demo
authoring:
  expose: true

# topologies/handle-item.yaml and topologies/morning-brief.yaml
#   both single-agent topologies using an openrouter model so a Space
#   run returns in a few seconds.
```

## 2. Boot

```bash
# terminal 1 — runtime
export OPENROUTER_API_KEY=sk-or-...
uv run swarmkit serve /tmp/spaces-demo --port 8099

# terminal 2 — dots app
cd reference/apps/dots
export SESSION_SECRET="$(openssl rand -hex 32)"
export DOTS_OWNER_PASSWORD_HASH="$(node scripts/hash-password.mjs 'change-me')"
SWARMKIT_URL=http://127.0.0.1:8099 pnpm dev -p 3509
```

Open http://localhost:3509, log in as `owner` / `change-me`.

## 3. The user journey

1. **Sidebar** shows the Dots section + the Spaces section with a `+` link.
2. Click `+` next to Spaces → `/spaces/new` → fill id, name, description →
   Create.
3. Lands on `/spaces/<id>` — three regions: activity rail (left), chat with a
   Dot picker (right), correlation_id in the header.
4. Chat with any Dot; swap the picker; chat with another Dot — same URL.
5. Activity rail polls every 5s and lists both runs. The runs share
   `correlation_id = space:<id>`.
6. Open the SwarmKit portal at `http://127.0.0.1:8099/jobs` — the Pipeline /
   correlation column shows both runs under the same `space:<id>`.

## 4. Capture the screenshots

```bash
cd reference/apps/dots
OPENROUTER_API_KEY=sk-or-... SWARMKIT_URL=http://127.0.0.1:8099 \
  ./scripts/capture-spaces-screenshots.sh
```

The script:

- resets `lib/spaces.local.json` to `[]` so the demo starts empty,
- boots the Next dev server on port 3509,
- runs `scripts/screenshot-spaces.mjs` which drives the full journey via
  Playwright and screenshots each step into `docs/spaces-screenshots/`,
- also hits `swarmkit serve`'s portal at `/jobs` so the capture includes the
  runtime's own view of the Space-correlated jobs — not just the dots app's
  own activity rail.

The script expects `swarmkit serve` to already be running (so you can point
it at any workspace you like, not just the throwaway demo one).
