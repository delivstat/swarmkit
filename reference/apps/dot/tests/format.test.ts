import { formatBriefDate, formatTimeShort, relativeShort } from "@/lib/format";
import { describe, expect, it } from "vitest";

describe("format", () => {
	it("labels the same UTC day as Today", () => {
		const iso = "2026-09-30T07:15:00Z";
		const now = new Date("2026-09-30T18:00:00Z");
		expect(formatBriefDate(iso, now)).toMatch(/^Today · /);
	});

	it("uses the plain date otherwise", () => {
		const iso = "2026-09-29T07:15:00Z";
		const now = new Date("2026-09-30T18:00:00Z");
		expect(formatBriefDate(iso, now)).not.toMatch(/^Today · /);
	});

	it("formats a short time", () => {
		expect(formatTimeShort("2026-09-30T10:00:00Z")).toBe("10:00 AM");
	});

	it("renders relative deltas", () => {
		const now = new Date("2026-09-30T10:00:00Z");
		expect(relativeShort("2026-09-30T10:45:00Z", now)).toBe("in 45m");
		expect(relativeShort("2026-09-30T13:00:00Z", now)).toBe("in 3h");
		expect(relativeShort("2026-09-28T10:00:00Z", now)).toBe("2d ago");
		expect(relativeShort("2026-09-30T09:15:00Z", now)).toBe("45m ago");
	});
});
