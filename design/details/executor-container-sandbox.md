---
status: accepted
---

# Executor container sandbox + egress proxy (opt-in)

The harness sandbox is a lie of omission today. `SandboxHandle.network` is `"deny"` on every handle
(`executors/_sandbox.py`), but nothing enforces it — a harness subprocess launched by
`_open_stream` (`executors/_declarative.py`) inherits the host's full network and, outside a
worktree, the host filesystem. The worktree gives us *isolation of edits*, not *isolation of the
process*. This note adds the real boundary: an **opt-in container tier** with resource limits and
**enforced** egress control, defaulting off so nothing changes for anyone who doesn't ask for it.

## Goal / non-goals

**Goal.** A workspace can declare that a harness archetype runs inside a container (docker or
podman) with CPU/memory/pid limits and a network policy that is actually enforced — `deny` (no
egress) or `allowlist` (egress only to named hosts, via a proxy). Turning it on is one adapter
field; turning it off globally is one env var.

**Non-goals.** Not the default (worktree stays the default; §7 "least surprise"). Not a
gVisor/Firecracker/microVM story — OS-container isolation is the tier we ship; stronger kernels are
a later escalation (see `OpenClaw` in the plan's Deferred section). Not a Kubernetes/pod runtime —
local `docker run` / `podman run` only. Not sandboxing the `model` executor (no subprocess to
contain) or MCP servers (already sandboxed — see Precedent).

## Precedent — reuse, don't reinvent

MCP servers already run under Docker: `mcp/_client.py::_build_sandboxed_command` does
`docker run -i --rm --network=none -v <ws>:/workspace:ro -w /workspace -e … <image> <cmd>`,
image from `SWARMKIT_SANDBOX_IMAGE`, and **raises** (never silently degrades) if `docker` is absent.
The executor tier follows the **same conventions** — image env var, `--rm`, `-e` env injection,
fail-loud-when-missing — with three deliberate differences the harness forces:

1. **Read-write mount.** A coding harness *produces a diff*; the worktree is bind-mounted `rw` at the
   container's working dir, not `:ro`. Diff collection (`collect_diff`) then works unchanged because
   the host worktree is the same directory the container wrote into.
2. **Network is usually not `none`.** A cloud harness (claude-code) must reach its model API, so
   `--network=none` would break it. `deny` suits a *local-model* harness; **`allowlist` is the
   important mode** for cloud harnesses — permit exactly the model/API hosts, nothing else. This is
   why the egress proxy exists rather than just `--network=none`.
3. **Resource limits.** MCP servers are short stdio bridges; a harness can spin for minutes, so
   `--cpus` / `--memory` / `--pids-limit` matter.

## Opt-in + the disable switch (the invariant this note must hold)

Three layers, most-specific wins, but **disable always wins**:

1. **Default = native worktree.** No adapter `sandbox` block, no env → today's behavior, byte for
   byte. Zero cost, zero new dependency at runtime.
2. **Opt in per archetype** via the adapter's `sandbox.kind: container` (below).
3. **Global disable** — `SWARMKIT_DISABLE_CONTAINER_SANDBOX=1` (and a `serve` config flag) forces
   the native worktree **regardless** of adapter config, logging one line that it did so. This is
   the escape hatch for an environment with no container runtime, a CI box, or a fast local loop —
   the operator is never trapped by an archetype that insists on a container.

Precedence, resolved once in `_sandbox_for`:

```
disable switch set?          → worktree   (log: "container sandbox disabled by env")
sandbox.kind == container?   → container  (error if no runtime AND no disable)
else                         → worktree   (unchanged)
```

Choosing `container` when no runtime is present is an **error, not a fallback** — the operator asked
for isolation; silently running unsandboxed would be a security lie (mirrors the MCP `raise`). The
way out is the disable switch, which is explicit.

## The adapter `sandbox` block (data, not code)

Isolation is declared where everything harness-specific already lives — the `adapter.yaml`
(`executor-adapter.schema.json` + `_adapter_spec.py`). New optional top-level block:

```yaml
sandbox:
  kind: container            # worktree (default) | container
  image: my-harness:latest   # a prebuilt image you choose; or use `build:` below
  network: allowlist         # deny (default) | allowlist
  allow: [api.anthropic.com] # hosts permitted when network: allowlist
  resources:
    cpus: "2"                # --cpus
    memory: 2g               # --memory
    pids: 512                # --pids-limit
```

**On the image: SwarmKit publishes none, and requires none.** Name the container one of three
ways — `build:` from a standard public base (below, the recommended "no local install" path),
`image:` pointing at any image you already trust, or an org-wide `$SWARMKIT_HARNESS_IMAGE` default.
`kind: container` with none of the three set is a **clear error, not a guessed base image** — a
guessed image likely wouldn't have the harness installed. A thin convenience base (git + common
tooling, mirroring the existing `swarmkit-mcp-sandbox`) *may* be published later through the Docker
build already in CI, but it is a convenience, never a dependency.

All fields optional; an absent block ≡ `kind: worktree`. `ResolvedExecutor.config` also accepts a
`sandbox` override so a workspace can tune limits per archetype without forking the adapter (same
pattern as `working_dir` / `allowed_tools`). The spec is validated at load; unknown network mode or
a non-container `kind` with an `allow` list is a schema error.

## Install the harness in the sandbox — no local install (the onboarding win)

The container tier unlocks something the worktree never could: **the user need not have the harness
installed at all.** Instead of a prebuilt `image`, an adapter can declare how to *build* one:

```yaml
sandbox:
  kind: container
  build:
    base: node:22-slim
    install: ["npm install -g @anthropic-ai/claude-code"]
  network: allowlist
  allow: [api.anthropic.com]
```

The runtime builds a **derived image once**, content-addressed by a hash of the resolved Dockerfile
+ context, tags it (e.g. `swarmkit-harness/<adapter-id>:<hash>`), and reuses it on every subsequent
run — so the install cost is paid once, not per run. The user brings only their **API key or
subscription** (injected at run via `-e`, never baked into the image — the build must not need the
credential). This makes the bundled adapters genuinely turnkey: `claude-code` ships a `build` block,
and a fresh workspace runs a sandboxed claude-code with nothing installed locally but Docker.
`image` and `build` are mutually exclusive (schema-enforced): use a prebuilt image, or build one.

**`base + install` vs a Dockerfile.** `build` accepts exactly one of three front-ends, all lowered
to a single Dockerfile the builder consumes:
- `base` + `install:` — the ergonomic 90% path (`FROM <base>` + a `RUN` per step); keeps the adapter
  a **single self-contained YAML**, nothing else to ship. Recommended for shareable adapters.
- `dockerfile_inline:` — full Docker control (COPY, multi-stage, ARG, ENV) while *still* keeping the
  adapter self-contained.
- `dockerfile:` — a path to a Dockerfile the user already has (context = its directory).
Content-addressing hashes the *resolved* Dockerfile, so all three cache identically.

**Shipped (task #19).** `_image.py`: `resolve_dockerfile` lowers all three front-ends to one
Dockerfile; `image_tag` content-addresses by `(adapter_id, dockerfile)` → `swarmkit-harness/<id>:<hash>`;
`build_harness_image` builds once (via a temp Dockerfile + the chosen context so `COPY` works) and
reuses the tag if it already exists. Wired into `container_sandbox` image resolution (prebuilt
`image`/`$SWARMKIT_HARNESS_IMAGE` wins; else the `build` block is built + cached). `swarmkit adapters
build <id>` warms the cache ahead of a run. A gated real-docker e2e builds a derived image with a
tool installed and runs it. (A `dockerfile` that `COPY`s changing context is not re-hashed — a
documented v1 limitation.)

Build is opt-in and lazy — no `build` block, or `kind: worktree`, means nothing is ever built.
Rebuild triggers only on a hash change (edit the base or install steps). A `swarmkit adapters build
<id>` command can warm the cache ahead of a run; a missing runtime is the same fail-loud error as
everywhere else.

## Resources the harness needs — mounts + MCP reachability

A harness in a locked-down container can reach only what we hand it. Three kinds of resource:

1. **The source tree** — already the worktree, bind-mounted `rw` at the working dir. Nothing to add.
2. **Extra directories** (a knowledge base, a shared read-only config, a second source tree) — the
   `sandbox.mounts` list: `{source, target, mode}`, `source` relative to the workspace root, default
   `ro`. Secrets never go here — they take the auth/`-e` path.
3. **MCP tools** — the workspace's MCP servers the harness is allowed to call (already named per task
   via `TaskSpec.mcp_tools`). Reachability depends on transport:
   - **HTTP/SSE MCP servers** — reachable by adding their `host:port` to the egress `allow` list (or
     attaching the harness to the same internal docker network as the server). This is the v1 path
     and needs no new field — it falls out of the network allowlist.
   - **stdio MCP servers** — a subprocess speaking over pipes can't be reached across a container
     boundary. v1 does **not** bridge these into the sandbox; the options (run the stdio server as a
     sidecar on the sandbox's network, or a host-side stdio→HTTP shim) are a **later refinement**,
     noted not built. Until then, a containerized harness uses HTTP/SSE MCP servers, and the loader
     warns if an archetype pairs a container sandbox with a stdio-only MCP tool.

The through-line: everything the harness can touch — filesystem, network, tools — is declared, so the
container is deny-by-default and we widen it explicitly, the same posture as the capability grant.

**Shipped (task #20).** `sandbox.mounts` is bind-mounted into the container (`_mount_args`: relative
`source` → workspace root, absolute used as-is, `mode` preserved). `_mcp_reach.py::mcp_reachability`
(pure, duck-typed) extracts **http** MCP servers' hostnames and lists **stdio** ids;
`_effective_allow` merges the http hostnames into an `allowlist` (so a containerized harness reaches
them) and logs a warning naming any stdio MCP server (can't cross the boundary — not bridged). The
workspace's MCP configs reach the sandbox via `MCPClientManager.configs`, threaded
`compiler → run_harness_node(mcp_manager=) → _sandbox_for → container_sandbox`. A gated e2e mounts a
KB dir read-only and reads it from inside a real container.

## Where it slots in — one chokepoint

`_sandbox_for(agent, root, base_ref)` (`_harness_node.py`) is the single place the sandbox is
chosen, and `_open_stream(argv, env, cwd, run_id)` (`_declarative.py`) is the single place the
subprocess is spawned. The container tier lives entirely behind these two:

- `_sandbox_for` gains a third branch → `container_sandbox(root, base_ref, spec.sandbox)`, a new
  `@asynccontextmanager` in `executors/_container.py` that provisions the container and yields a
  `SandboxHandle(root=<host worktree>, kind="container", network=<mode>, exec_prefix=[...])`.
- `SandboxHandle` gains an `exec_prefix: tuple[str, ...] = ()`. For a worktree it's empty (spawn
  `argv` directly, as today). For a container it's the `docker exec <id>` (or the wrapping
  `docker run … <image>`) prefix, so `_open_stream` spawns `exec_prefix + argv`. **The auth-env
  stripping in `_launch_env` is unchanged** — the same filtered env is handed to the container via
  `-e` (secrets never touch the image, matching the MCP path).

Two provisioning shapes, decided in the note, implemented in task #13:
- **run-per-invocation** (`docker run … <image> <argv>`): simplest, one container per node run,
  `--rm` cleanup. Chosen for v1 — matches the harness lifecycle (one run = one container).
- long-lived `create`+`exec` (for park-resume relaunches to reuse a warm container) is a later
  optimization; park-resume already re-runs the process, so v1 provisions fresh each relaunch.

## Network enforcement (the part worth building)

`SandboxHandle.network` stops being advisory:

- **`deny`** → `--network none`. Nothing leaves the container. Correct for a fully local harness.
- **`allowlist`** → the container joins an internal network with **no default route to the
  internet**; a small **egress proxy** (an HTTP/HTTPS forward proxy, e.g. a tinyproxy/squial-style
  container the runtime brings up on that internal network) is the only reachable next hop, and it
  permits `CONNECT`/GET only to hosts in `allow`. The harness env gets `HTTPS_PROXY` /
  `HTTP_PROXY` / `NO_PROXY` injected (via the same `-e` path) so a well-behaved client routes
  through it; the missing default route means a client that *ignores* the proxy simply can't reach
  anything. Blocked attempts are logged by the proxy and surfaced as an audit event where
  observable.

The proxy is itself a container the runtime manages for the lifetime of the sandbox (brought up in
`egress_for.__aenter__`, torn down in `__aexit__`), so there is no host-level daemon to install.
`allow` defaults to empty; an `allowlist` with no hosts is effectively `deny` plus a proxy (useful
as a strict base to add to). v1 enforces at the **host/proxy** layer (route + proxy ACL); per-host
TLS pinning and DNS-level filtering are noted as later hardening.

**Shipped (task #14).** `_egress.py::egress_for` implements exactly this: `deny` → `--network none`;
`allowlist` → an `--internal` docker network + a **tinyproxy** forward proxy (default-deny, one
anchored-regex filter per `allow` host, HTTPS `CONNECT` permitted), dual-homed onto the bridge for
its own egress, with `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` (upper + lower case) injected inline.
Consistent with "SwarmKit publishes no image", the proxy is a **1-line inline Dockerfile
(`alpine` + `tinyproxy`) built locally, once, content-addressed + cached** — no published artifact.
A gated real-docker e2e proves `deny` blocks a raw dial and `allowlist` permits only the listed host
(the proxy's own logs show non-listed hosts refused).

## PR slices (map to tasks)

1. **Design** — this note. (task #11)
2. **Config seam** — `sandbox` block in schema + `_adapter_spec.py` (dual-language codegen),
   `ResolvedExecutor` plumbing, the disable switch + precedence in `_sandbox_for`, `SandboxHandle`
   gains `exec_prefix`. Native path unchanged; container branch stubbed to a clear "not yet"
   `ExecutorError`. Unit: spec parse, schema validity, disable precedence. (task #12)
3. **Provisioner** — `executors/_container.py`: runtime detection (docker|podman via `shutil.which`,
   podman preferred if both? — no, honor `$SWARMKIT_CONTAINER_RUNTIME`, else docker, else podman),
   `docker run` assembly with rw mount + resource limits, `exec_prefix` wiring in `_open_stream`,
   fail-loud when absent, `--rm` teardown. Unit with a fake runtime (assert argv); gated e2e
   (`SWARMKIT_E2E=1` + a real runtime) runs a trivial harness in-container and collects its diff.
   (task #13)
4. **Network enforcement** — `deny` → `--network none`; `allowlist` → internal network + managed
   egress-proxy container + `HTTPS_PROXY` injection + proxy ACL from `allow`. Unit: mode → runtime
   args mapping. Gated e2e: `deny` blocks a real outbound call; `allowlist` permits only a listed
   host. (task #14)
5. **Build-in-sandbox** — `sandbox.build` → a derived image built once, content-addressed + cached,
   with `swarmkit adapters build`. The "no local install" path. (task #19)
6. **Resource mounts + MCP reachability** — `sandbox.mounts` bind-mounts; HTTP/SSE MCP via the egress
   allowlist; the loader warning for a container + stdio-only MCP pairing. (task #20)
7. **Demo + docs + PR** — `demos/container_sandbox.py`, adapter-authoring guide + a discipline note,
   version bump, PR, CI, publish. (task #15)

## Test plan

- **Unit (no runtime):** sandbox-block parsing + schema round-trip; disable-switch precedence
  (`container` + disable → worktree); runtime-arg assembly against a fake `which`/subprocess
  (mount is `rw`, limits present, `network none` vs proxy env, auth env passed via `-e` and not
  baked); "container requested, no runtime, no disable → `ExecutorError`".
- **Gated e2e (`SWARMKIT_E2E=1`, real docker/podman):** run a minimal harness adapter in a container,
  assert its file edit lands in the host worktree and `collect_diff` sees it; `network: deny` makes
  an outbound `curl` fail; `network: allowlist:[host]` permits that host and blocks another.
- **Regression:** the full existing harness/relay/trust suite passes with no `sandbox` block (proves
  opt-in default is untouched).

## Demo plan

`uv run python packages/runtime/demos/container_sandbox.py` — run a harness archetype with
`sandbox.kind: container`, show the run happening inside the container (id, resource limits), the
diff coming back to the host worktree, an egress attempt to a non-allowlisted host being blocked,
then re-run with `SWARMKIT_DISABLE_CONTAINER_SANDBOX=1` and show it fall back to the native worktree
with the one-line notice. Skips with a clear message when no container runtime is present.

## Acceptance

- No `sandbox` block anywhere ⇒ identical behavior + identical dependencies to today (opt-in).
- `sandbox.kind: container` runs the harness in a container with the declared limits; its diff still
  reaches the host worktree; secrets reach the container only via `-e`, never the image.
- `network: deny` blocks all egress; `network: allowlist` permits only listed hosts; blocked
  attempts are audited where observable.
- `container` requested with no runtime present ⇒ a clear `ExecutorError` naming the disable switch —
  never a silent unsandboxed run.
- `SWARMKIT_DISABLE_CONTAINER_SANDBOX=1` forces the native worktree for every archetype, logged once.
- A `sandbox.build` adapter runs the harness with **nothing installed locally** but a container
  runtime; the derived image is built once and reused; the credential reaches it only via `-e`.
- `sandbox.mounts` makes a knowledge-base dir readable inside the container; an HTTP/SSE MCP server
  on the `allow` list is reachable; a container + stdio-only MCP pairing warns at load.
- Eject story: the container flags (run args, mounts, build ref) are derivable from the adapter's
  `sandbox` block, so an ejected LangGraph node can reproduce the `docker build`/`docker run`
  wrapper (invariant #7).
