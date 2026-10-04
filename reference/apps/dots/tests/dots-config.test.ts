import { DOTS, findDot } from "@/lib/dots.config";
import { describe, expect, it } from "vitest";

describe("dots.config", () => {
	it("declares at least one Dot", () => {
		expect(DOTS.length).toBeGreaterThan(0);
	});

	it("findDot returns the right Dot and undefined otherwise", () => {
		const first = DOTS[0];
		expect(first).toBeDefined();
		if (!first) return;
		expect(findDot(first.id)?.name).toBe(first.name);
		expect(findDot("does-not-exist")).toBeUndefined();
	});

	it("every Dot has non-empty required fields", () => {
		for (const dot of DOTS) {
			expect(dot.id).toMatch(/^[a-z][a-z0-9-]*$/);
			expect(dot.topology).toBeTruthy();
			expect(dot.name).toBeTruthy();
			expect(dot.role).toBeTruthy();
			expect(dot.greeting).toBeTruthy();
		}
	});
});
