# Eval set

An **eval set** (`kind: EvalSet`) is the list of cases a topology is scored against. Each case runs the target topology on an input and checks the output. The case passes when every check set on it passes. `swarmkit eval` runs a set and exits 1 if any case fails, so CI can gate on it.

An eval set is the **test** step of growth through authoring (design §12): find a gap, author a fix, *test* it, publish it. The schema is `eval-set.schema.json` in `packages/schema`, and the runtime validates every set against it before running. The design record is [`design/details/eval-set-schema-v1.md`](https://github.com/delivstat/swarmkit/blob/main/design/details/eval-set-schema-v1.md), and how the harness scores cases is in [Eval harness](../design-notes/eval-harness.md).

## Where it lives

Eval sets are YAML files under `evals/` in the workspace. `swarmkit eval <workspace> <ref>` takes the set's `metadata.id`, its filename stem, or a path to a file anywhere:

```bash
swarmkit eval ./workspace greeting-evals            # by id, under ./workspace/evals/
swarmkit eval ./workspace ./candidate-evals.yaml    # by path
swarmkit eval ./workspace greeting-evals --compare  # and diff against the previous run
```

## Schema shape

```yaml
apiVersion: swarmkit/v1
kind: EvalSet
metadata:
  id: support-triage-evals          # lowercase-kebab; what `swarmkit eval` names
  name: Support triage              # optional display name
  description: Routes each ticket to the right queue and never promises a refund.
target: support-triage@1.2.0        # the topology every case runs; `@x.y.z` pins one version
cases:
  - id: billing-goes-to-billing     # lowercase-kebab, unique within the set
    input: I was charged twice for my subscription this month.
    expect:
      contains: [billing]
      not_contains: [refund approved]
      used_skills: [classify-ticket]
      judge: triage-quality         # a decision skill in the workspace
      min_confidence: 0.7
  - id: outage-is-urgent
    input: Nothing loads and the status page is down.
    expect:
      regex: "(?i)urgent|p1"
      rubric: The reply treats this as an outage and escalates it.
provenance:                         # optional
  authored_by: human
  version: 1.0.0
```

## Fields

| Field | Required | What it does |
|---|---|---|
| `metadata.id` | yes | The set's id, in lowercase kebab. |
| `metadata.name`, `metadata.description` | no | Shown to the person reviewing the set. |
| `target` | yes | The topology the cases run against. A bare name runs the workspace's current version. `name@x.y.z` pins one version, for example a canary candidate. Cases are never routed through a canary split, so a report always scores one version. |
| `cases` | yes, at least one | The cases, run in order. |
| `cases[].id` | yes | Lowercase kebab, **unique within the set**. Reports and `--compare` are keyed by case id, so keep a case's id when you edit it. |
| `cases[].input` | yes | What the topology is run on: the same text a caller sends to `POST /run`. Must not be empty. |
| `cases[].expect` | no | The checks. Empty or absent makes the case a smoke test that passes when the run completes. |
| `provenance` | no | The shared artifact provenance block. |

## Checks (`expect`)

Deterministic checks are free. `judge` and `rubric` each make one model call.

| Check | Passes when |
|---|---|
| `contains: [..]` | every string appears in the output (case-insensitive) |
| `not_contains: [..]` | no string appears in the output (case-insensitive) |
| `regex: ".."` | the Python regular expression matches somewhere in the output (`re.search`) |
| `equals: ".."` | the whole output, trimmed, equals this. Model output varies, so use it only for deterministic topologies |
| `not_empty: true` | the output contains something other than whitespace |
| `used_skills: [..]` | the run invoked every listed skill, read from its events |
| `judge: <skill-id>` | the decision skill returns `pass` at `min_confidence` or above |
| `rubric: ".."` | the workspace's judge model scores the output `pass` against this rubric at `min_confidence` or above |
| `min_confidence` | the threshold for `judge` and `rubric`, from 0 to 1. The default is 0.5 |

## Validation

A set is checked against the schema before anything runs, and an error names the field:

```text
error: ./set.yaml @ /cases/0/expect: Additional properties are not allowed ('contain' was unexpected)
```

Unknown keys are refused at every level, so a typo can't silently turn a check off. Two rules are checked by the runtime, because JSON Schema can't express them:

- case ids are unique within the set;
- the target names a topology in the workspace. This is checked when the set is run.

Since runtime 1.266.0, a set with an unknown key, no cases, a non-kebab id, an empty input or a duplicate case id is refused. Before that, those were ignored.

## Reports

Each run prints a per-case PASS/FAIL with the failing checks, and a pass rate. It stores a JSON report under `.swarmkit/eval-results/`, and `--output PATH` writes another copy. `--compare` diffs the run against the previous one, listing cases that regressed, were fixed or are new.
