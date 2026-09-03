---
title: Skill catalogue — a curated library whose "works" is a fact with a date
description: A separately-versioned repo of pre-wired skills across all four backings, kept honest by a liveness check that runs against the real servers and files an issue when one breaks.
tags: [skills, ecosystem, distribution, validation]
status: draft
---

# Skill catalogue

**Scope:** a new public repo (`swarmkit-skills`), plus `requires_runtime` in the skill schema and a
`swarmkit skill` command group
**Supersedes:** the middle layer and the trust model of `skill-registry.md` — see
[What this changes](#what-this-changes-about-skill-registrymd)
**Status:** draft

**Companion:** [`skill-catalogue-seed.md`](skill-catalogue-seed.md) decides *what goes in it* —
the first hundred servers, triangulated across six sources and ordered by what the liveness check
can actually verify.

## Goal

Make the common case — *"I want this MCP server as a skill"* — a one-line command instead of forty
minutes of trial and error, and make the claim that a catalogue entry **works** something with a
date on it rather than a promise.

## The gap is wiring, not skills

*"Any of 7,260 MCP servers can be a skill"* is true and about as useful as *"any package works with
pip."* What is missing per server is the part nobody publishes:

- the `mcp_servers` block — command, `env`, `cwd`, whether it needs `sandboxed: true`
- the `permission` tier, and now the per-tool `effects` map that `readonly` requires
- the skill YAML with an `iam.required_scopes` that is neither too broad nor missing
- the argument shape the tool actually wants, which its README rarely states precisely

Every user rediscovers that independently, for the same twenty servers. Thirty entries where
somebody already did it beats eleven hundred that might work.

Two signals that this is a real gap rather than a nice-to-have:

- **`reference/skills/` has 27 skills and not one uses the `command` backing** shipped in 1.197.0.
  The newest extension type has no library at all.
- **`reference/command-packs/` is already a second location.** There are two homes for curated
  artifacts before anyone has asked for a catalogue.

## Verification is the product

This is the part that decides whether the repo is worth building.

An `mcp_tool` skill depends on a server somebody else maintains. That server can rename a tool,
change an argument, add auth, or disappear. A curated list nobody re-checks becomes an awesome-list,
and those rot in months — there are already several awesome-MCP repos, and being another one adds
nothing.

**SwarmKit can do what a list structurally cannot: start the server and ask.**

```
nightly, per entry:
  1. resolve requires  — is the binary / image / package still fetchable?
  2. start the server  — does it come up inside the declared timeout?
  3. list_tools        — does the named tool still exist?
  4. compare schema    — does its inputSchema still match what the skill declares?
  5. dry-run           — for a read-only tool, does one call return without error?
```

That turns *"pre-validated"* into **"verified 2026-09-01 against server v2.3"** — a fact with a
date, which is a different kind of claim from a promise. Every entry carries its last-verified
timestamp, and the catalogue's front page is a table of them.

**Build this first, not last.** Bolted on afterwards it never gets built, and the repo becomes the
thing it was meant to replace.

### When a check fails

**Mark it broken, file an issue, do not quietly fix.**

```yaml
status:
  state: broken                       # verified | broken | unmaintained
  since: 2026-09-01
  reason: "tool 'get_pull_request' no longer listed; server exposes 'pull_request_read'"
  issue: delivstat/swarmkit-skills#212
```

A broken entry stays visible and stays honest. Removing it silently is worse: someone who already
copied it learns nothing, and the catalogue looks healthier than it is.

The issue is the interface to whoever fixes it — **and "whoever" can be a swarm.** A broken entry is
a well-specified authoring task with a machine-checkable acceptance test: the liveness check that
failed. That is the Skill Authoring Swarm's exact job description, and it closes the loop the
product claims as its third pillar — *swarms grow through human-approved authoring, gated at every
step*. The fix arrives as a PR a human approves; nothing self-modifies.

An entry `broken` for three consecutive checks with no fix becomes `unmaintained`, which is a signal
to a reader rather than a deletion.

## Compatibility is a version floor, and we have paid for forgetting

A `command`-backed skill needs runtime ≥1.197.0. One using `requires:` needs ≥1.193.0. One relying
on a `readonly` MCP server with declared `effects` needs ≥1.199.0.

**Skills carry no compatibility field today.** Without one, a catalogue entry resolves cleanly into
an older workspace and fails at run time with an error that names nothing useful.

This is precisely the `swarmkit-webui` failure: a separately-versioned artifact that must match the
runtime's surface, with an unbounded floor. It froze at 0.14.0 and shipped a portal navigating to an
API removed a week earlier — cleanly resolvable, broken in a browser, and reported by a user rather
than caught by CI.

So the schema gains one field:

```yaml
provenance:
  authored_by: human
  version: 1.2.0
  requires_runtime: ">=1.197.0"       # new
```

Checked at workspace load, refusing with the version it needs and the version present. A floor that
fails at import is worth more than a diagnosis at run time.

`provenance` is `additionalProperties: false`, so this is a real schema change and follows
`docs/notes/schema-change-discipline.md` — canonical schema, the bundled `_schemas/` copy, fixtures
valid and invalid, both codegens. Adding it is cheap now and a migration across every published
catalogue entry later, which is the same argument that put `frame_path` into a fingerprint from v1.

## Importing one skill writes to two places

The uncomfortable part of `swarmkit skill add github-pr-read` is not fetching. `swarmkit install`
already takes a URL, so distribution is nearly free. It is that one skill needs **two** edits:

```
skills/github-pr-read.yaml       ← new file, safe
workspace.yaml  mcp_servers:     ← a hand-authored artifact, not safe
```

Editing someone's `workspace.yaml` silently is the kind of convenience people switch off. So:

```bash
swarmkit skill add github-pr-read              # shows the diff, asks, applies
swarmkit skill add github-pr-read --dry-run    # prints both fragments, writes nothing
swarmkit skill list --available                # the catalogue, with verification dates
swarmkit skill check                           # re-run the liveness check on what is installed
```

`--dry-run` is the honest default for the first version: print the YAML, let the user paste it. Full
auto-edit is the second version, once the diff has been boring for a while.

## Where things live

| | holds | versioned with |
| --- | --- | --- |
| `reference/` in this repo | the **worked examples the docs teach from** — small, stable, illustrative | the runtime |
| `swarmkit-skills` (new) | the **catalogue** — breadth, third-party servers, liveness-checked | itself, floored per entry |

Not three locations. `reference/command-packs/` and `reference/skills/` stay as documentation
material; anything that depends on an external service belongs in the catalogue, because that is
what needs a nightly check and a version of its own.

## Non-goals

- **Bundling the catalogue into the runtime.** A broken skill would then need a runtime release to
  fix, coupling two things that change at very different rates. `skill-registry.md` proposed the
  registry ship inside `swarmkit-runtime`; that is the part this note reverses.
- **A rating or marketplace system.** Verification state is the only signal, and it is objective.
- **Importing 1,100 `SKILL.md` files.** Breadth without verification is the failure mode being
  designed against. The converter in `skill-registry.md` stays proposed and unbuilt for now.
- **Auto-updating installed skills.** An import is a copy into the user's workspace and stays theirs.
  `swarmkit skill check` tells them something upstream changed; it does not change their file.

## What this changes about `skill-registry.md`

That note (status: proposed, April 2026) stays the reference for the *external* ecosystem — the
`SKILL.md` format, the converter, the catalogue landscape. Two of its decisions are reversed here:

| `skill-registry.md` | this note | why |
| --- | --- | --- |
| the registry ships **inside `swarmkit-runtime`** | a **separate repo** | a skill fix should not need a runtime release |
| skills are **"trusted by source"** (Anthropic, Google, MCP official) | skills are **verified nightly**, and say when | provenance is not liveness; a trusted publisher's server still renames tools |

The second is the substantive one. "Trusted by source" is an assumption with no expiry date, and it
is exactly what turns a curated list into a stale one.

## Test plan

- **Unit** — `requires_runtime` parsing and comparison; a skill above the floor loads, one below is
  refused with both versions named; an absent field means no constraint.
- **Unit** — the import planner produces both fragments and mutates nothing under `--dry-run`.
- **Integration** — `swarmkit skill add` against a fixture catalogue: new skill file, `mcp_servers`
  diff shown, applied only on confirmation; re-running is idempotent.
- **Integration** — the liveness check against a local stub MCP server: passes when the tool matches,
  reports `broken` with the tool name when renamed, and does not throw when the server refuses to
  start.
- **Catalogue CI** (in the new repo) — every entry validates against `skill.schema.json`, declares a
  `requires_runtime`, and names a maintainer. Schema validity is a merge gate; liveness is nightly,
  because a third-party outage must not block a PR.

## Demo plan

`just demo-skill-catalogue` — add a skill from a fixture catalogue into a scratch workspace with
`--dry-run` (printing both fragments), then for real; run the liveness check green; rename the tool
in the stub server; re-run and watch the entry go `broken` with the reason and a filed-issue link.

The rename is the demo. Anyone can show an install working; the claim worth demonstrating is that
the catalogue *notices* when it stops.

## Open questions

1. **Verification needs somewhere to run.** Nightly, some entries need Docker, some need network,
   some need credentials. Credentialed servers (GitHub, Slack) cannot be fully checked in public CI —
   they are probably `unverifiable` as an honest third state rather than a silent pass.
2. **What is the catalogue's unit?** One skill, or a bundle per server — "the GitHub pack" with six
   skills sharing one `mcp_servers` entry. The bundle is closer to how people actually adopt one, and
   maps onto command packs, which suggests bundle.
3. **How does an entry pin the upstream server?** A version, a digest, or nothing. Nothing makes the
   liveness check meaningful but the install unreproducible; a digest is the opposite.
4. **The first fifteen.** Chosen by what people actually reach for, which we do not know yet — the
   first honest source is what shows up in issues.
