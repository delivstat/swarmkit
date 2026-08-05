# The portal ships only if `swarmkit-webui` is bumped

`packages/ui` is the source; `swarmkit-webui` is the wheel that carries its built bundle. They have
**separate versions**, and the publish workflow uses `publish_if_new` — it uploads a package only
when its version is not already on PyPI.

So a change to `packages/ui` with no bump to `packages/webui/pyproject.toml` means:

- the bundle is rebuilt at release time and **does** contain the change,
- the wheel version is unchanged, so publish **skips** it,
- every workflow goes green and the release reports success,
- and no user ever receives the fix.

Caught once, at v1.145.0: the jobs-history page (UI 0.30.0) was in the built bundle while
`swarmkit-webui` still said 0.7.0, which was already published.

## The rule

**Changing anything under `packages/ui/` requires bumping `packages/webui/pyproject.toml` too.**

The UI's own `package.json` version is not enough — nothing publishes from it. The webui wheel is
what reaches an operator running `pip install "swarmkit-runtime[ui]"`.

## Checking before a tag

The portal bundle is generated, not committed, so a diff will not tell you. Compare against what is
already published:

```bash
curl -s https://pypi.org/pypi/swarmkit-webui/json \
  | python -c "import json,sys; print(sorted(json.load(sys.stdin)['releases'])[-3:])"
grep '^version' packages/webui/pyproject.toml
```

If the version in the tree is already on PyPI **and** anything under `packages/ui/` changed since
that release, the bump was missed.

This is the same shape as the rest of `reported-bugs.md`: the information exists, nothing surfaces
it, and the absence renders as success.
