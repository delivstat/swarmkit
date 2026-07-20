# Workspace environment configuration

Separate environment-specific values (URLs, credentials, feature flags) from structural workspace config so the same workspace runs in dev, staging, and prod without editing `workspace.yaml`.

There are two layers, and you can use either or both:

- **Env references in any artifact** (runtime 1.98.0+) — `${VAR}`, `${VAR:-default}`, and `$${VAR}` resolve in **every** artifact (topology, skill, archetype, workspace, trigger, funnel), with or without an env file. This is the quick path for making a reusable library model- or endpoint-configurable. See [Env references in any artifact](#env-references-in-any-artifact) below.
- **The workspace property map** — a `workspace.env.yaml` file that maps dotted `${property.path}` references in `workspace.yaml` to real values, with per-environment overrides. This is the structured path for the two-file dev/staging/prod split described in the rest of this page.

The two layers compose: a `${NAME}` reference resolves from the property map first, then the OS environment, then a `:-default`, then is left literal.

## Env references in any artifact

Any string in any artifact can reference the environment — no env file required. This is resolved at load time, before schema validation, so the runtime and validators see the resolved value.

```yaml
# archetypes/reasoner.yaml — ships working out-of-the-box, overridable per deployment
apiVersion: swarmkit/v1
kind: Archetype
metadata:
  id: reasoner
defaults:
  model:
    provider: ${SDLC_REASONING_PROVIDER:-openrouter}
    name: ${SDLC_REASONING_MODEL:-moonshotai/kimi-k2.5}
```

Syntax:

- **`${VAR}`** — the value of `VAR`.
- **`${VAR:-default}`** — `VAR` if set, else `default`. Defaults let a reusable library run out-of-the-box while staying configurable.
- **`$${VAR}`** — a literal `${VAR}` (escape), for the rare artifact that must contain the sequence.

Resolution order for each `${NAME}`:

1. **Workspace property map** — dotted paths from `workspace.env.yaml` (the layer documented below); empty when there is no env file.
2. **OS environment** — `os.environ[NAME]`.
3. **Inline default** — the text after `:-`.
4. **Left literal** — an unresolved reference with no default is emitted unchanged, so artifacts that already contain `${...}` never regress.

Because an unresolved reference is left literal rather than raising, enabling this across all artifacts is backward compatible: workspaces with no env file and no references behave exactly as before.

## File layout

```
workspace/
├── workspace.yaml              # structural — committed to git
├── workspace.env.yaml          # environment — add to .gitignore
├── workspace.env.prod.yaml     # optional per-environment override
├── topologies/
├── skills/
└── archetypes/
```

## How it works

`workspace.yaml` uses `${property.path}` references instead of inline values:

```yaml
# workspace.yaml — safe to commit, no secrets
mcp_servers:
  - id: github
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: ${github.token}

notifications:
  - provider: slack
    config:
      webhook_url: ${notifications.slack.webhook_url}
      channel: ${notifications.slack.channel}
```

`workspace.env.yaml` provides the actual values:

```yaml
# workspace.env.yaml — NOT committed to git
# This is the ONLY file that does ${ENV_VAR} interpolation

github:
  token: ${GITHUB_TOKEN}

notifications:
  slack:
    webhook_url: ${SLACK_WEBHOOK_URL}
    channel: "#swarmkit-dev"
```

## Resolution order

1. **`workspace.env.{SWARMKIT_ENV}.yaml`** — if `SWARMKIT_ENV` is set and the file exists
2. **`workspace.env.yaml`** — default fallback
3. **`${ENV_VAR}`** in property values — resolved from OS environment
4. **Inline values** in `workspace.yaml` — backward compatible, used as-is if no `${...}` reference

## Environment switching

```bash
# Dev (default — uses workspace.env.yaml)
swarmkit run my-swarm/ my-topology

# Production
SWARMKIT_ENV=prod swarmkit run my-swarm/ my-topology

# Staging
SWARMKIT_ENV=staging swarmkit run my-swarm/ my-topology
```

Each environment can have its own env file with different credentials, endpoints, and feature flags.

## Two-phase interpolation

1. **Phase 1:** Load env file → flatten nested YAML to dotted paths (`notifications.slack.channel` → `#swarmkit-dev`)
2. **Phase 2:** Resolve `${ENV_VAR}` in property values from OS environment (`${GITHUB_TOKEN}` → actual token)
3. **Phase 3:** Replace `${property.path}` references in workspace.yaml with resolved values

Keeping `${ENV_VAR}` in the env file concentrates secret interpolation in one place. (Env references also work directly in any artifact — see [Env references in any artifact](#env-references-in-any-artifact) — but routing secrets through the env file keeps them auditable in a single file.)

## Backward compatibility

Existing workspaces without `workspace.env.yaml` work unchanged. Property references (`${...}`) are only resolved if the `${}` syntax is present. If you don't create an env file, nothing changes.

## Best practices

- **Add `workspace.env*.yaml` to `.gitignore`** — never commit credentials
- **Route secrets through the env file** — put `${ENV_VAR}` for credentials in `workspace.env.yaml` so secret interpolation stays auditable in one place, even though env references work in any artifact
- **Create a `workspace.env.example.yaml`** with placeholder values for team onboarding
- **Use named env files for each environment** — `workspace.env.dev.yaml`, `workspace.env.staging.yaml`, `workspace.env.prod.yaml`

## Example

### workspace.yaml (committed)

```yaml
apiVersion: swarmkit/v1
kind: Workspace
metadata:
  id: my-swarm
  name: My Swarm

mcp_servers:
  - id: github
    transport: stdio
    command: ["npx", "-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: ${github.token}

governance:
  provider: agt
  config:
    policies_dir: ${governance.policies_dir}
```

### workspace.env.yaml (dev, not committed)

```yaml
github:
  token: ${GITHUB_TOKEN}

governance:
  policies_dir: ./policies
```

### workspace.env.prod.yaml (prod, not committed)

```yaml
github:
  token: ${GITHUB_TOKEN_PROD}

governance:
  policies_dir: /etc/swarmkit/policies
```
