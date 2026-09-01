# A package ships only if its version moves

The publish workflow uses `publish_if_new`: it uploads a package only when its version is not
already on PyPI. So a package whose **content** changed but whose **version** did not is skipped —
silently, with every workflow green and the release reporting success.

The repo has four publishable packages with four independent versions:

| Package | Version lives in | Content comes from |
| --- | --- | --- |
| `swarmkit-runtime` | `packages/runtime/pyproject.toml` | `packages/runtime/src` |
| `swarmkit-schema` | `packages/schema/python/pyproject.toml` | `packages/schema/schemas` |
| `swarmkit-webui` | `packages/webui/pyproject.toml` | `packages/ui` (built bundle) |
| `swarmkit-control-plane` | `packages/control-plane/pyproject.toml` | `packages/control-plane/src` |

Two of those have a version that lives in a **different package** from the source you edit. That is
where both failures happened.

## It has happened three times

**`swarmkit-webui` — caught before tagging v1.145.0.** The jobs-history page was in the rebuilt
bundle while the wheel version was still 0.7.0, already published. Editing `packages/ui` does not
change `packages/webui/pyproject.toml`, and nothing publishes from the UI's own `package.json`.

**`swarmkit-schema` — not caught; it shipped.** The version sat at 1.23.0 from 2026-07-27 through
six releases while `packages/schema/schemas` kept changing. Everyone who installed SwarmKit got a
schema that rejected:

- `server.auth.config.identity` / `identity_name` (1.140.0) — the named local operator
- `server.auth.config.client_id` / `scope` (1.141.0) — the portal's OIDC client
- `storage.artifacts` (from the storage-service work)
- the declarative adapters' `*_map` tables (1.135.0) — so the runtime shipped bundled `codex` and
  `gemini-cli` adapters that its own published schema rejected

Four features were merged, tested, reviewed, released, announced — and unreachable.

**`swarmkit-webui` again — not caught; it shipped, and a user reported it.** 0.14.0 was cut on
2026-08-07. The bundled pipeline was removed from the runtime a week later, and six commits changed
the UI. The version never moved, so every release skipped the upload — and the *published* portal
kept a **Pipelines** section navigating to `POST /pipelines/*`, an API that no longer existed. The
route rendered, then failed. There was no version to upgrade to.

The bundle was never the problem: `_static/` is gitignored and the publish workflow rebuilds it from
current source before `uv build`, so every wheel built since carried a correct portal. None was
uploaded.

## The rule, and the check

**Before tagging, run:**

```bash
uv run python scripts/check_publishable.py
```

It compares each package's local version against PyPI and against **the commit that last set that
version**, and fails when a changed package would be skipped. Verified against history: it flags
v1.137.0 and v1.141.0, the two tags where the schema silently did not ship.

### The baseline is the whole trick

The first version of this check compared against the **last tag**, and that is why it missed both
shipped failures. A version frozen across several releases stops looking changed the moment its
change falls out of that one-tag window — which is precisely the situation the check exists for.
Against the last tag, `swarmkit-webui` showed **one** changed file; against its own version commit,
**fourteen**.

Two smaller rules came from the same incident:

- **A package's `pyproject.toml` is watched content.** Dependencies, extras and entry points ship in
  the wheel metadata, so tightening a version floor is a change users need. The version line itself
  is not a false positive: the baseline is the commit that *set* it, and `git diff <commit>..HEAD`
  excludes that commit, so only later edits count.
- **Tests are not watched content.** A `.test.ts` beside the source it tests lives inside a package
  directory and reaches no artifact. Counting it demands a bump that would publish a byte-identical
  release, and a PyPI version is permanent — the opposite failure to the one this exists for.

**The check reads committed history.** Run it after the version-bump commit; before it, a
working-tree edit is invisible and everything looks clean.

A rule you have to remember is not a rule. The changelog generator exists for the same reason —
a claim about a release should be executable, not hoped for.

## Related

`docs/notes/schema-change-discipline.md` covers *what to touch* when a schema changes (source,
bundled copy, fixtures, codegen). This note covers what to bump so the change reaches anyone.
[`RELEASING.md`](../../RELEASING.md) is the step-by-step runbook that applies both.
