# Harness adapter discipline

Executors are **data**: a harness is an `adapter.yaml`, interpreted by the one `DeclarativeExecutor`. Contributor-facing how-to lives in `docs/guides/authoring-harness-adapters.md`; this note is the "don't forget" checklist for people changing the adapter surface.

## Verify against the real binary before claiming an adapter works

Fixture tests prove the *engine* and your event-map logic — **not** fidelity to the real tool. An adapter authored from documentation alone is a guess (we shipped a guessed `opencode` map that was entirely wrong until the real binary corrected it). So:

- Run the real harness once, capture its actual `--json`/`stream-json` output, and make the `event_map` match it.
- Keep an adapter marked **EXPERIMENTAL** in its `metadata.description` until it has been run end-to-end against a real binary.
- The gated e2e (`SWARMKIT_E2E=1 uv run pytest packages/runtime/tests/test_harness_e2e.py`, binary on PATH) is the fidelity check. Add verified harnesses to its `VERIFIED_HARNESSES` list.

## `claude-code.yaml` exists in two places — keep them byte-identical

The reference adapter is committed twice and both must stay in sync:

- `packages/runtime/src/swarmkit_runtime/executors/adapters/claude-code.yaml` — the **bundled** adapter used at runtime (ships in the wheel).
- `packages/schema/tests/fixtures/executor-adapter/claude-code.yaml` — the **schema fixture** the runtime unit tests (`test_event_map.py`, `test_declarative_executor.py`) parse.

If you edit one, copy it to the other:

```bash
cp packages/runtime/src/swarmkit_runtime/executors/adapters/claude-code.yaml \
   packages/schema/tests/fixtures/executor-adapter/claude-code.yaml
```

## Adding a bundled adapter

1. Write `packages/runtime/src/swarmkit_runtime/executors/adapters/<id>.yaml` (it validates against `executor-adapter.schema.json`).
2. It ships in the wheel automatically (hatchling includes package data) and is registered automatically in `default_executor_registry` — no code change.
3. Add a captured stream fixture under `packages/runtime/tests/fixtures/harness-streams/<id>.jsonl` and a case in `test_bundled_adapters.py`.
4. Verify against the real binary (above) before removing the EXPERIMENTAL marker.

## Result status across multiple fields

If a harness signals errors with more than one field, classify with **ordered result rules — last match wins** (the interpreter keeps one terminal `ExecResult` per line). Do not try to force it through a single `status_map` on one field. See `claude-code.yaml` (`is_error` + `subtype`).

## Launch review gate

A **workspace** adapter's `launch` block is human-gated (`swarmkit adapters approve <id>`); bundled adapters are pre-vetted and bypass. Changing a workspace adapter's launch surface invalidates its approval. This is enforced in `_build_executor` — don't add a bypass.

## Sandbox is opt-in — never silently unsandboxed

The `sandbox` block (`kind: container`) is opt-in; absent, the harness runs in the native worktree exactly as before. Two rules to keep: `SWARMKIT_DISABLE_CONTAINER_SANDBOX` **always wins** (forces the worktree, regardless of adapter config — resolved once in `_sandbox_for`), and a container requested with **no runtime present must fail loud**, never fall back to an unsandboxed run (that would be a security lie — mirrors the MCP Docker sandbox's `raise`). The `image`/`build` credential rule: secrets reach the container only via `-e` at run, never baked into the image or a `build` step. Design: `design/details/executor-container-sandbox.md`.

## Schema changes

Changing `executor-adapter.schema.json` follows the general [schema-change discipline](schema-change-discipline.md): sync the bundled `_schemas/` copy, regenerate pydantic + TS (`just schema-codegen`), add fixtures, run both validators.
