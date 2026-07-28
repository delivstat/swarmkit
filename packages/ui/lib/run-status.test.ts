import { describe, expect, it } from "vitest";

import { stageStatus, stageTimeline } from "./run-status";
import type { SagaDetail } from "./types";

const base = {
	status: "active" as const,
	current_stage: "design",
	passed_stages: ["intake"],
	pending_gate_stage: null as string | null,
};

describe("stageStatus", () => {
	it("marks a passed stage passed", () => {
		expect(stageStatus(base, "intake")).toBe("passed");
	});

	it("marks the current stage active", () => {
		expect(stageStatus(base, "design")).toBe("active");
	});

	it("marks an unreached stage pending", () => {
		expect(stageStatus(base, "build")).toBe("pending");
	});

	it("parked gate wins for its stage", () => {
		const parked = {
			...base,
			status: "parked" as const,
			pending_gate_stage: "design",
		};
		expect(stageStatus(parked, "design")).toBe("parked");
	});

	it("reflects a terminal failure on the current stage", () => {
		const failed = {
			...base,
			status: "failed" as const,
			current_stage: "build",
		};
		expect(stageStatus(failed, "build")).toBe("failed");
	});

	it("a passed stage stays passed even after the run rejects elsewhere", () => {
		const rejected = {
			...base,
			status: "rejected" as const,
			current_stage: "design",
		};
		expect(stageStatus(rejected, "intake")).toBe("passed");
		expect(stageStatus(rejected, "design")).toBe("rejected");
	});
});

describe("stageTimeline", () => {
	it("returns only a stage's entries, in seq order", () => {
		const saga = {
			timeline: [
				{ seq: 3, at: "t3", stage: "design", kind: "parked", detail: "" },
				{ seq: 1, at: "t1", stage: "intake", kind: "started", detail: "" },
				{ seq: 2, at: "t2", stage: "design", kind: "started", detail: "" },
			],
		} as SagaDetail;
		const rows = stageTimeline(saga, "design");
		expect(rows.map((r) => r.seq)).toEqual([2, 3]);
	});
});
