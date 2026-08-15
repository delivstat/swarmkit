/**
 * Stopping and resuming a run from the portal (design/details/stopping-a-run.md).
 *
 * `swarmkit stop` and these buttons are peers: both write the same durable flag, so an operator
 * watching the Jobs page can stop a run without a terminal, and the CLI can stop one the portal
 * started.
 *
 * Two things worth pinning in the UI layer rather than only in the runtime:
 *
 * - **Which statuses offer which control.** A `running` job can be stopped; a `deferred` or
 *   `stopped` one can be resumed; a finished one offers neither. Getting this wrong shows a button
 *   that 409s.
 * - **The status union covers what the server sends.** It stopped at `failed`, so a parked run had
 *   a status TypeScript said was impossible — a type that excludes real states is worse than
 *   `string`, because it makes a wrong narrowing look checked.
 */

import { describe, expect, it } from "vitest";
import { canResume, canStop } from "./run-controls";
import type { JobStatus } from "./types";

describe("which control a status offers", () => {
	it("offers Stop only while the run might still do something", () => {
		expect(canStop("running")).toBe(true);
		expect(canStop("pending")).toBe(true);
		expect(canStop("completed")).toBe(false);
		expect(canStop("failed")).toBe(false);
	});

	it("offers Resume for a run parked on a gate and for one a human stopped", () => {
		expect(canResume("deferred")).toBe(true);
		expect(canResume("stopped")).toBe(true);
	});

	it("offers neither once the run is over", () => {
		for (const status of ["completed", "failed"] as JobStatus[]) {
			expect(canStop(status)).toBe(false);
			expect(canResume(status)).toBe(false);
		}
	});

	it("never offers both at once — a run is either live or parked", () => {
		const every: JobStatus[] = [
			"pending",
			"running",
			"completed",
			"failed",
			"deferred",
			"stopped",
			"interrupted",
		];
		for (const status of every) {
			expect(canStop(status) && canResume(status)).toBe(false);
		}
	});

	it("leaves an interrupted run alone", () => {
		// The process went away mid-run; whether the checkpoint is usable is not something the
		// portal can assert, so it offers nothing rather than a button that may 409.
		expect(canStop("interrupted")).toBe(false);
		expect(canResume("interrupted")).toBe(false);
	});
});
