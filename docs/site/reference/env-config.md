# Workspace environment configuration

Separate environment-specific values (URLs, credentials, feature flags) from structural workspace config so the same workspace runs in dev, staging, and prod without editing `workspace.yaml`.

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

This means environment variables are only interpolated in the env file, not scattered across all YAML files.

## Backward compatibility

Existing workspaces without `workspace.env.yaml` work unchanged. Property references (`${...}`) are only resolved if the `${}` syntax is present. If you don't create an env file, nothing changes.

## Best practices

- **Add `workspace.env*.yaml` to `.gitignore`** — never commit credentials
- **Use `${ENV_VAR}` only in the env file** — keeps interpolation in one place
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
