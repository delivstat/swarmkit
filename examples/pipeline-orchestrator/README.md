# A pipeline orchestrator that lives outside SwarmKit

SwarmKit runs a swarm over an input and returns a governed, approved artifact. **Deciding what runs
next belongs to the application.** This is what that looks like: a sequencer over the public HTTP
API, with no `swarmkit_runtime` import anywhere.

See `design/details/extracting-the-pipeline.md` for why, and
`design/details/finishing-the-orchestration-seam.md` for the boundary this demonstrates.

## The whole integration

```
POST /run/{topology}          -> job_id
poll GET /jobs/{job_id}
      running    -> keep polling
      completed  -> take the artifact
      deferred   -> an approval is pending
      failed     -> your retry policy
on deferred:
      GET /review              -> find the gate whose run_id is this job
      poll GET /gates/{gate_id}
            approved  -> POST /jobs/{job_id}/resume
            rejected  -> the stage fails
```

Start, poll, wait for a human, resume. Nothing about it is SwarmKit-specific — a Temporal workflow
expresses it with activities and a signal, a cron job with `curl` and `sleep`. That is the argument
for the boundary: the sequencing is ordinary, and only the run and the approval are not.

## Five endpoints

| call | why |
| --- | --- |
| `POST /run/{topology}` | start a stage, correlated |
| `GET /jobs/{id}` | watch it; `status` and `diff_length` |
| `GET /review` | find the gate a parked run waits on — each role-task carries `run_id` |
| `GET /gates/{id}` | is it resolved, **with quorum and exclude_author applied** |
| `POST /jobs/{id}/resume` | continue after approval |

Plus `GET /jobs/{id}/diff` and `GET /artifacts/{ref}` for what a run produced.

**Do not count approved role-tasks yourself.** Quorum, distinct-approver floors and `exclude_author`
live in the funnel, which your application does not read. `GET /gates/{id}` applies them. An
approximation of an approval policy is a governance failure with a friendly name.

## Run it

```bash
swarmkit serve ./workspace &          # SwarmKit: runs, governs, gates
python run_pipeline.py WMS-35         # your application: decides what runs next
```

## What your application owns

- which stages exist, in what order, on what trigger
- retries, backoff, dead-letter
- durability of the *sequence* (this example keeps it in memory; a real one uses your database,
  or Temporal's history)

The unit of durability in SwarmKit is the **run**. A run that parks on a gate survives a restart on
its own; the sequence around it is yours to make durable.

## What SwarmKit still owns

Topologies, archetypes, skills and funnels. Running a swarm, validating and judging its output,
parking for approval, resuming, and the audit and cost trail — all correlated by the id you pass.

## Files

- `orchestrator.py` — the sequencing loop (~180 lines, mostly comments)
- `client.py` — httpx over `swarmkit serve`
- `run_pipeline.py` — a two-stage example you can point at a workspace
- `tests/` — the loop driven against a scripted server, including the acceptance test that this app
  never imports the runtime
