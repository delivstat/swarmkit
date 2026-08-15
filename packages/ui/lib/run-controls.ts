/**
 * Which run controls a job status offers (design/details/stopping-a-run.md).
 *
 * In `lib/` rather than inline in the page so the rules are testable as themselves. A copy of these
 * predicates in a test file would pass while the page drifted — which is how a button that 409s
 * ships green.
 */

import type { JobStatus } from "./types";

/** A run in flight can be asked to stop. It lands at the next agent boundary. */
export function canStop(status: JobStatus): boolean {
	return status === "running" || status === "pending";
}

/**
 * A run parked mid-flight can be resumed. `deferred` (waiting on a gate) and `stopped` (a human
 * asked) are both parked with their state on the checkpoint; only the reason differs.
 *
 * `interrupted` is deliberately absent: the process went away mid-run, and whether the checkpoint
 * is usable is not something the portal can assert — so it offers nothing rather than a button that
 * may fail.
 */
export function canResume(status: JobStatus): boolean {
	return status === "deferred" || status === "stopped";
}
