# Level 21: Providers, storage and operations

The operator's level: which model endpoints an instance can reach, where its records live, how to
score a topology before shipping it, and how to hand the whole thing to an LLM.

## What you'll learn

- Declarative model providers: a new endpoint is a YAML file, not a release
- `swarmkit providers list/show` — what is ready, and why not
- One storage service: `swarmkit storage status`, SQLite → Postgres with `storage migrate`
- `swarmkit system` — versions, properties, environment, with secrets masked by declaration
- The eval harness: `swarmkit eval` gating CI on a topology's behaviour
- Artifacts and comprehension debt from the record: `artifacts`, `comprehension`
- `swarmkit knowledge-pack` — the corpus, paste-ready

## The idea

An operator's questions are all "what will this instance actually do?": which URL will a run hit,
which database will it write, is the key set, did the last change make the topology worse. Each has a
command that answers from the resolved configuration, not from a guess — and every one of them is
also an HTTP endpoint the portal's System page and the fleet panel read.

## Build it

### 1. Add a provider without touching Python

Twelve providers ship bundled over four wire-format families (`anthropic`, `google`,
`openai-compatible`, `ollama`). One more is a file:

```yaml
# providers/vllm.yaml
apiVersion: swarmkit/v1
kind: ModelProvider
metadata: { id: vllm, name: vLLM, description: vLLM's OpenAI-compatible server on the GPU box. }
spec:
  extends: openai-compatible
  base_url: ${VLLM_URL:-http://gpu-box:8000/v1}
  models: { accept_any: true }
  capabilities: { images: false }        # may narrow the family, never widen it
provenance: { authored_by: human, version: 1.0.0 }
```

```bash
swarmkit providers list ./workspace
```

```
  anthropic     anthropic          bundled     ready (ANTHROPIC_API_KEY set)
  groq          openai-compatible  bundled     needs GROQ_API_KEY
  ollama        ollama             bundled     ready (no auth)
  vllm          openai-compatible  workspace   ready (no auth)
```

`providers show vllm` prints the resolved chain — what actually reaches the family. Then
`provider: vllm` in a topology, or `SWARMKIT_PROVIDER=vllm` for one run. A declared
`capabilities.images: false` refuses a run that attaches an image at the request, rather than sending
bytes a server would drop.

### 2. Know where the records go

```bash
swarmkit storage status ./workspace
```

```
  store        backend   location         (source)
  runtime      sqlite    workspace-local  (default)
  audit        sqlite    workspace-local  (default)
  checkpoints  sqlite    workspace-local  (default)
  artifacts    sqlite    workspace-local  (default)
  memory       sqlite    workspace-local  (default)
  fleet        sqlite    workspace-local  (default)
```

One service resolves every store; nothing else opens a database. Moving to Postgres:

```yaml
# workspace.yaml
storage:
  runtime: { backend: postgres, url: ${SWARMKIT_STORE_URL} }
```

```bash
export SWARMKIT_STORE_URL=postgresql://…
swarmkit storage status ./workspace            # every store says postgres BEFORE you move data
swarmkit storage migrate ./workspace --dry-run
swarmkit storage migrate ./workspace --yes     # additive, idempotent, never deletes the SQLite files
```

A backend naming a real database with no resolvable URL **refuses to start** rather than writing to
SQLite while reporting success. `checkpoints` follows only its own block — it needs the
`[postgres]` extra and degrades to SQLite with a warning, because a checkpoint is disposable run
state.

### 3. What is this instance?

```bash
swarmkit system ./workspace        # versions, storage resolution, workspace.env.yaml properties, environment
curl http://127.0.0.1:8000/system  # the same, from a running serve; GET /storage for the stores alone
```

`workspace.env.yaml` declares properties and a reserved `secrets:` list of dotted paths whose values
render as `set` — here, on the portal's System page, in CI logs. A name-based heuristic
(`key`/`token`/`secret`/`password`) is the fallback; declaring can add to the masked set, never
remove from it.

### 4. Score a topology before you ship it

```yaml
# evals/greeting-evals.yaml
apiVersion: swarmkit/v1
kind: EvalSet
metadata: { id: greeting-evals, description: The hello swarm greets the named audience. }
target: hello
cases:
  - id: greets-and-not-empty
    input: Greet the engineering team
    expect: { not_empty: true }
  - id: addresses-the-audience
    input: Greet the engineering team
    expect: { regex: "(?i)engineer|team" }
```

```bash
swarmkit eval ./workspace greeting-evals --compare      # exit 1 if any case fails; --compare diffs the previous run
```

```
  [PASS] greets-and-not-empty
  [FAIL] addresses-the-audience
         ✗ regex: (?i)engineer|team
1/2 passed (50%) · report: .swarmkit/eval-results/greeting-evals-<timestamp>.json
```

(That is the mock provider answering "mock response" — the failing case is the point: a check that
cannot fail is not a check.) Deterministic checks, rubric judges and trajectory checks all live in the
same file; results are stored, so `--compare` names regressions and fixes.

### 5. Read the record

```bash
swarmkit artifacts list <correlation-id> ./workspace   # every artifact recorded under one ticket
swarmkit artifacts get <ref> ./workspace
swarmkit comprehension ./workspace                     # comprehension-debt signals from the audit log — read-only, never a gate (GET /comprehension on serve)
swarmkit logs ./workspace --last 5
swarmkit trace <run-id> ./workspace
```

### 6. Hand it to an LLM

```bash
swarmkit knowledge-pack --lean -o swarmkit-pack.md    # ~190k tokens: overview, generated references, schemas, design doc, guides
swarmkit knowledge-pack -o swarmkit-full.md           # ~610k: plus every design note, the tutorials, historical ones last under a banner
```

## Run it

```bash
just demo-providers        # declarative providers resolved through their chains
bash examples/storage-service/demo.sh   # the one storage service; status, migrate --dry-run, a misconfiguration refused
just demo-comprehension    # comprehension-debt signals on a recorded run
just demo-knowledge-pack   # the pack, and what it contains
```

## What happened

Every answer came from the resolved configuration: the provider chain, the storage decision and its
source, the masked property set, the eval report. None of it required a run to find out.

## Learn more

- [Model provider artifact](../reference/model-provider.md) · [Storage](../reference/storage.md) · [Environment configuration](../reference/env-config.md)
- [Telemetry configuration](../reference/telemetry.md) — OpenTelemetry to Jaeger/Prometheus
- [Eval harness](../design-notes/eval-harness.md) · [Storage service](../design-notes/storage-service.md)
