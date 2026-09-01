# Releasing SwarmKit

The runbook. Follow it in order — several steps only work in this order, and the notes say why.

**One tag releases four independently-versioned packages.** Pushing `v1.x.y` triggers the PyPI
publish and the Docker build. Each package uploads only if *its own* version is new; the tag name
is not the version of anything.

| package | version lives in | content comes from |
| --- | --- | --- |
| `swarmkit-runtime` | `packages/runtime/pyproject.toml` | `packages/runtime/src` + its `pyproject.toml` |
| `swarmkit-schema` | `packages/schema/python/pyproject.toml` | `packages/schema/schemas` |
| `swarmkit-webui` | `packages/webui/pyproject.toml` | `packages/ui` — built at publish time |
| `swarmkit-control-plane` | `packages/control-plane/pyproject.toml` | `packages/control-plane/src` |

**Two of those keep their version in a different package from the source you edit**, which is where
every release failure so far has come from.

---

## 1. Bump every package whose content changed

Not just the runtime. Then:

```bash
just release-check
```

It compares each package against **the commit that last set its version** and fails if content
changed while the version is already on PyPI — because `publish_if_new` would silently skip it, with
every workflow green and the release reporting success.

Read the failure rather than working around it. It has caught this three times and missed it twice,
and both misses shipped to users. Full history: [`docs/notes/release-version-discipline.md`](docs/notes/release-version-discipline.md).

> **The guard reads committed history.** A working-tree edit is invisible to it, so run it *after*
> the commit in step 2, not before. Running it first is how a change looks clean and ships stale.

**A dependency change is a content change.** `pyproject.toml` is watched for every package:
tightening a version floor ships in the wheel metadata and needs a bump like any other edit.

## 2. Commit the bump

```bash
git add -A && git commit -m "chore(release): swarmkit-runtime 1.x.y"
just release-check          # now it can see it
```

## 3. Build and look inside the artifacts

```bash
uv build --all-packages
```

All four must succeed. Then **check the wheel carries what you think it does** — the workflow saying
"success" is not the same as the artifact being right:

```bash
python3 -c "
import zipfile
z = zipfile.ZipFile('dist/swarmkit_runtime-1.x.y-py3-none-any.whl')
print([n for n in z.namelist() if 'commands/' in n][:5])
"
```

> **`uv build` does not build the portal.** The publish workflow runs
> `pnpm --filter @swarmkit/ui build` and copies `packages/ui/out` into
> `packages/webui/src/swarmkit_webui/_static/` **before** `uv build`. A local `uv build` packages
> whatever is in `_static/` already, which may be nothing.
>
> If you stage it by hand to inspect the bundle, the `rm -rf _static` in that step **deletes the
> tracked `.gitkeep`**. Restore it before committing:
> ```bash
> git checkout packages/webui/src/swarmkit_webui/_static/.gitkeep
> ```

## 4. Tag — and write the message as a changelog entry

```bash
git tag -a v1.x.y -m "SwarmKit v1.x.y — <summary>"
```

**The tag subject *is* the changelog entry.** `just changelog` reads annotated tags verbatim, so a
vague message is permanent. Write the sentence you would want to read in six months:

> ✅ `command packs: local commands as a skill implementation type`
> ❌ `misc fixes and improvements`

## 5. Push the tag

```bash
git push origin v1.x.y
```

This triggers `publish` (PyPI) and `docker`, both on `tags: ["v*"]`.

**PyPI never allows re-uploading a version.** If you push the tag before bumping, the publish fails
and recovery means deleting the tag, bumping, and re-tagging. Always bump first.

## 6. Verify against PyPI, not against the workflow

A green workflow can mean "uploaded" or "skipped everything". Check the log:

```bash
RUN=$(gh run list --limit 10 --json databaseId,name,headBranch \
      --jq '[.[] | select(.name=="publish" and .headBranch=="v1.x.y")][0].databaseId')
gh run view $RUN --log | grep -iE "Uploading|already on PyPI — skipping"
```

Then install what you published, in a clean environment:

```bash
uv venv /tmp/relcheck && VIRTUAL_ENV=/tmp/relcheck uv pip install --refresh "swarmkit-runtime==1.x.y"
VIRTUAL_ENV=/tmp/relcheck uv run --no-project python -c "import swarmkit_runtime.commands"
```

> **PyPI's JSON API lags.** `pypi.org/pypi/<pkg>/json` can still show the previous version for a few
> minutes after a successful upload. Do not treat that as a failed publish — the workflow log and an
> actual install are the truth. (I once diagnosed a "silent skip" that was pure index lag.)

## 7. Regenerate the changelog — after the push

```bash
just changelog
```

It reads the annotated tags that **exist**, so this runs after step 5, in a follow-up docs PR. The
document claimed to be tag-generated for months while being hand-maintained and drifted 33 versions
behind; the last regeneration caught up 25 releases at once.

---

## If something goes wrong

| symptom | cause | fix |
| --- | --- | --- |
| `release-check` fails on a package you didn't touch | its version is older than a change to its content — often `packages/ui` for `swarmkit-webui` | bump it; that is the guard working |
| Publish succeeded, PyPI shows the old version | index lag, or the package was skipped | check the workflow log for `Uploading` vs `already on PyPI — skipping` |
| Tag pushed before the bump | PyPI rejected the upload | `git push --delete origin v1.x.y`, `git tag -d v1.x.y`, bump, re-tag |
| The portal is stale after release | `swarmkit-webui` version did not move, so the fresh bundle was never uploaded | bump `packages/webui/pyproject.toml` and re-release |
| `.gitkeep` shows as deleted | you staged the portal by hand in step 3 | `git checkout packages/webui/src/swarmkit_webui/_static/.gitkeep` |

## What a release does not do

- **It does not build the docs site.** That is `docs.yml`, on pushes to `main`.
- **It does not update the changelog.** Step 7, deliberately, and in its own PR.
- **It does not bump anything for you.** There is no auto-versioning; four packages move at four
  different rates and a tool guessing would be worse than the guard.
