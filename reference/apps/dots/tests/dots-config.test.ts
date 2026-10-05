import { promises as fs } from "node:fs";
import {
	BUILT_IN_DOTS,
	DOT_ID_PATTERN,
	findDot,
	loadDots,
	readLocalDots,
	writeLocalDots,
} from "@/lib/dots.config";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("dots.config", () => {
	beforeEach(() => {
		// Make readLocalDots return [] by default — some tests opt in to local fixtures.
		vi.spyOn(fs, "readFile").mockRejectedValue(
			Object.assign(new Error("ENOENT"), { code: "ENOENT" }),
		);
	});

	afterEach(() => {
		vi.restoreAllMocks();
	});

	it("declares at least one built-in Dot", () => {
		expect(BUILT_IN_DOTS.length).toBeGreaterThan(0);
	});

	it("every built-in Dot has non-empty required fields with a valid id", () => {
		for (const dot of BUILT_IN_DOTS) {
			expect(dot.id).toMatch(DOT_ID_PATTERN);
			expect(dot.topology).toBeTruthy();
			expect(dot.name).toBeTruthy();
			expect(dot.role).toBeTruthy();
			expect(dot.greeting).toBeTruthy();
		}
	});

	it("findDot returns a built-in by id when no local store exists", async () => {
		const first = BUILT_IN_DOTS[0];
		expect(first).toBeDefined();
		if (!first) return;
		await expect(findDot(first.id)).resolves.toMatchObject({ name: first.name });
		await expect(findDot("does-not-exist")).resolves.toBeUndefined();
	});

	it("local Dots merge with built-ins and win on id conflict", async () => {
		vi.spyOn(fs, "readFile").mockResolvedValue(
			JSON.stringify([
				{
					id: "morning-brief", // shadows built-in
					name: "Override",
					role: "r",
					icon: "sunrise",
					topology: "morning-brief",
					greeting: "g",
					renderers: [],
				},
				{
					id: "my-dot",
					name: "My Dot",
					role: "r",
					icon: "sunrise",
					topology: "x",
					greeting: "g",
					renderers: [],
				},
			]),
		);
		const dots = await loadDots();
		const mb = dots.find((d) => d.id === "morning-brief");
		expect(mb?.name).toBe("Override");
		expect(dots.find((d) => d.id === "my-dot")).toBeDefined();
	});

	it("readLocalDots returns [] on ENOENT", async () => {
		await expect(readLocalDots()).resolves.toEqual([]);
	});

	it("DOT_ID_PATTERN rejects obvious bad ids", () => {
		expect(DOT_ID_PATTERN.test("ok-dot")).toBe(true);
		expect(DOT_ID_PATTERN.test("ok")).toBe(true);
		expect(DOT_ID_PATTERN.test("1-bad")).toBe(false);
		expect(DOT_ID_PATTERN.test("Has-Caps")).toBe(false);
		expect(DOT_ID_PATTERN.test("")).toBe(false);
		expect(DOT_ID_PATTERN.test("space in it")).toBe(false);
	});

	// writeLocalDots is covered by a tiny round-trip test — it's a one-line fs.writeFile but
	// pinning that the file is JSON-shaped protects the loader on the other side.
	it("writeLocalDots writes JSON that readLocalDots can parse", async () => {
		let written: string | undefined;
		vi.spyOn(fs, "writeFile").mockImplementation(async (_p, data) => {
			written = String(data);
		});
		await writeLocalDots([
			{
				id: "x",
				name: "X",
				role: "r",
				icon: "sunrise",
				topology: "t",
				greeting: "g",
				renderers: [],
			},
		]);
		expect(written && JSON.parse(written)).toEqual([expect.objectContaining({ id: "x" })]);
	});
});
