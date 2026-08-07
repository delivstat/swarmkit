/**
 * A trace span says what a tool was asked and what it answered.
 *
 * The waterfall showed a tool's name and its duration. The span carried a character count and
 * nothing else — so a reader could see that `search-wms-tables` ran for 800ms and not what it
 * looked for or found, which is the question a waterfall is usually opened to answer.
 *
 * The trace keeps a BOUNDED copy of each result: a corpus search can return megabytes, and a run
 * with 300 calls would be unopenable in a browser. So the reader has to distinguish "this is the
 * whole answer" from "this is the start of it" — a truncated payload taken for the full one is a
 * worse failure than showing nothing.
 */

import { describe, expect, it } from "vitest";

import { toolDetail } from "./format";

const SPAN = {
	"swarmkit.tool.name": "sterling__search_docs",
	"swarmkit.tool.arguments": { query: "PGM hold", limit: 40 },
	"swarmkit.tool.result": "3 tables: pgm_hold, pgm_hold_type, order_hold",
	"swarmkit.tool.result_length": 44,
	"swarmkit.tool.cached": false,
};

describe("toolDetail", () => {
	it("reads the full tool name, arguments and result", () => {
		const detail = toolDetail(SPAN);

		expect(detail?.name).toBe("sterling__search_docs");
		expect(detail?.arguments).toEqual({ query: "PGM hold", limit: 40 });
		expect(detail?.result).toContain("pgm_hold");
	});

	it("is null for a span that is not a tool call", () => {
		/** Agent and run spans share the waterfall; only tool rows open. */
		expect(toolDetail({ "swarmkit.executor.kind": "model" })).toBeNull();
	});

	it("reports a truncated result as truncated", () => {
		/** The trace keeps a bounded copy. A reader taking the first 2000 characters for the whole
		 * answer would draw conclusions from a payload that was cut off. */
		const detail = toolDetail({
			...SPAN,
			"swarmkit.tool.result": "x".repeat(2000),
			"swarmkit.tool.result_length": 58_000,
		});

		expect(detail?.truncated).toBe(true);
	});

	it("does not call a complete result truncated", () => {
		expect(toolDetail(SPAN)?.truncated).toBe(false);
	});

	it("survives a span from before results were recorded", () => {
		/** Older traces have a length and no result. That must read as "not recorded", not as an
		 * empty answer — and must not throw and take the whole waterfall down. */
		const detail = toolDetail({
			"swarmkit.tool.name": "search",
			"swarmkit.tool.result_length": 120,
		});

		expect(detail?.result).toBe("");
		expect(detail?.resultLength).toBe(120);
		expect(detail?.truncated).toBe(true);
	});

	it("tolerates arguments that are not an object", () => {
		const detail = toolDetail({
			...SPAN,
			"swarmkit.tool.arguments": "not an object",
		});

		expect(detail?.arguments).toEqual({});
	});

	it("carries the cached flag", () => {
		expect(toolDetail({ ...SPAN, "swarmkit.tool.cached": true })?.cached).toBe(
			true,
		);
	});
});
