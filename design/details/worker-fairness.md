---
title: Worker fairness — workload-class pools and queue priority
description: Route long harness runs and short model runs to separate worker pools so one cannot starve the other, and order the queue by an explicit priority. Follow-up to worker-execution.md from the scaling review.
tags: [serve, scaling, operations, workers]
status: proposed
---

# Worker fairness

## The gap

`worker-execution.md` gave one durable FIFO drained by identical workers. A review noted the
failure mode: workloads have wildly different shapes. A `harness` run holds a session, edits files,
spawns subprocesses, and takes minutes; a `model` run is seconds. In one FIFO with one pool, a burst
of harness runs occupies every worker and short model runs wait behind them — a queue-wait cliff for
the cheap work. Ordering was `created_at` only, so there was also no way to push an urgent run ahead.

## Goal

- **Workload-class pools.** Every queued job carries a `job_class` (`model` | `harness`), derived
  from the topology. A worker claims only the classes it is for (`swarmkit worker --class harness`),
  so a model pool and a harness pool drain independently and neither starves the other.
- **Priority within a class.** A `priority` integer (higher first, then oldest) lets an urgent run
  jump the queue without a separate mechanism.
- Both default to today's behaviour: a worker with no `--class` claims any class; a job with no
  priority is 0.

## Non-goals

- **Not per-tenant quotas / weighted fairness.** Isolating classes and honouring a priority is the
  starvation fix; fair-sharing across tenants (a max in-flight per tenant, weighted round-robin) is
  a larger scheduler and a named follow-up, not this.
- **Not auto-scaling pools.** How many workers of each class to run is an operator decision
  (`swarmkit worker --class model` ×N, `--class harness` ×M); the runtime does not spawn them.
- **Not a new executor concept.** The class is *derived* from the existing executor kinds
  (`executor-abstraction.md`), not a new field an author sets.

## Design

### Class is derived, not declared

At enqueue (`serve --role api`), the API tier walks the resolved topology's agent tree: if any agent
runs on a **harness** executor the job is `harness`, else `model`. It is written to the durable
`jobs.job_class` alongside `status: queued`. Deriving it means an author never sets it and it cannot
drift from what the topology actually does.

### Claim filters by class, orders by priority

```sql
UPDATE jobs SET status='running', worker_id=:w, lease_until=…, claimed_at=now()
WHERE id = (SELECT id FROM jobs
            WHERE status='queued' AND job_class = ANY(:classes)      -- omitted when --class any
            ORDER BY coalesce(priority,0) DESC, created_at
            FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id;
```

`swarmkit worker --class model` passes `classes={'model'}`; `--class any` (default) passes none and
claims everything, so a single undifferentiated pool still works. Priority comes from a `priority`
label on submit (opaque to the runtime otherwise), parsed as an int, default 0.

### Two additive columns

`job_class` (Text) and `priority` (Integer, default 0) join the additive `jobs` migration. Old rows
and non-queue (all-in-one) rows read null/0 and are unaffected — only queue-mode enqueue sets them,
and only workers read them.

## Test plan

- Claim with a class filter takes only that class; a restricted pool leaves the other class alone
  (Postgres).
- Higher priority is claimed before lower, then oldest (Postgres).
- Class derivation: a harness executor anywhere in the tree ⇒ `harness`, else `model`; stubs without
  an executor default to `model`.
- Enqueue stamps `job_class` and the `priority` label onto the durable row.

## Demo plan

Two worker pools against one Postgres — `swarmkit worker --class model` and
`swarmkit worker --class harness` — draining a mixed backlog; the model pool keeps completing short
runs while long harness runs occupy the harness pool.
