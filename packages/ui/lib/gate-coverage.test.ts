import { describe, expect, it } from "vitest";
import {
	COVERAGE_COLOR,
	coverageByStage,
	edgeCoverageColor,
} from "./gate-coverage";
import type { GateCoverage } from "./types";

const COV: GateCoverage = {
	pipeline: "p",
	verdict:
		"narrowest verified edge: stage 'build' advances with no gate (passthrough).",
	narrowest: "build",
	stages: [
		{
			stage: "intake",
			gate: "passthrough",
			funnel: null,
			pre_filters: [],
			external_entry: true,
			terminal: false,
		},
		{
			stage: "design",
			gate: "human",
			funnel: "design-approval",
			pre_filters: ["validate", "judge", "review"],
			external_entry: false,
			terminal: false,
		},
		{
			stage: "build",
			gate: "passthrough",
			funnel: null,
			pre_filters: [],
			external_entry: false,
			terminal: false,
		},
	],
};

describe("coverageByStage", () => {
	it("indexes by stage id and flags the narrowest", () => {
		const m = coverageByStage(COV);
		expect(m.get("design")?.gate).toBe("human");
		expect(m.get("build")?.isNarrowest).toBe(true);
		expect(m.get("design")?.isNarrowest).toBe(false);
		expect(m.get("intake")?.external_entry).toBe(true);
	});

	it("returns an empty map for undefined coverage", () => {
		expect(coverageByStage(undefined).size).toBe(0);
	});
});

describe("edgeCoverageColor", () => {
	it("colors by the source stage's gate class", () => {
		const m = coverageByStage(COV);
		expect(edgeCoverageColor(m, "build")).toBe(COVERAGE_COLOR.passthrough);
		expect(edgeCoverageColor(m, "design")).toBe(COVERAGE_COLOR.human);
	});

	it("returns undefined for an unknown source (keep the default)", () => {
		expect(edgeCoverageColor(coverageByStage(COV), "nope")).toBeUndefined();
		expect(
			edgeCoverageColor(coverageByStage(undefined), "build"),
		).toBeUndefined();
	});
});
