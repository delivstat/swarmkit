import { describe, expect, it } from "vitest";
import { formatTokens, formatUsd } from "./format";

describe("formatUsd", () => {
	it("shows 2 decimals for cent-and-up costs", () => {
		expect(formatUsd(0.06)).toBe("$0.06");
		expect(formatUsd(1.5)).toBe("$1.50");
		expect(formatUsd(12.3456)).toBe("$12.35");
	});

	it("shows 4 decimals for sub-cent costs (so they don't round to $0.00)", () => {
		expect(formatUsd(0.0012)).toBe("$0.0012");
	});

	it("renders zero / invalid as $0.00", () => {
		expect(formatUsd(0)).toBe("$0.00");
		expect(formatUsd(-1)).toBe("$0.00");
		expect(formatUsd(Number.NaN)).toBe("$0.00");
	});
});

describe("formatTokens", () => {
	it("passes small counts through", () => {
		expect(formatTokens(0)).toBe("0");
		expect(formatTokens(842)).toBe("842");
	});

	it("compacts thousands + millions", () => {
		expect(formatTokens(1234)).toBe("1.2k");
		expect(formatTokens(1_200_000)).toBe("1.2M");
	});
});
