/**
 * The sidebar's persisted state and active-link rule.
 *
 * Eighteen nav entries sat in a `flex-col` with no overflow handling, inside a body that is
 * `h-screen overflow-hidden`. On a short viewport the entries past the fold were not awkward to
 * reach — they were unreachable, because nothing scrolled.
 *
 * These cover the parts that are logic rather than layout: remembering the collapse preference
 * (including that "never chosen" is not "expanded"), surviving a browser that refuses storage, and
 * matching the active link on a path boundary.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isActive, loadCollapsed, storeCollapsed } from "./sidebar-state";

/** A minimal localStorage. This package has no DOM environment configured and the whole suite is
 * pure logic, so a stub keeps it that way rather than pulling in jsdom for four assertions. */
function fakeStorage(over: Partial<Storage> = {}) {
	const data = new Map<string, string>();
	return {
		getItem: (k: string) => data.get(k) ?? null,
		setItem: (k: string, v: string) => {
			data.set(k, v);
		},
		removeItem: (k: string) => {
			data.delete(k);
		},
		...over,
	};
}

function withStorage(storage: unknown): void {
	vi.stubGlobal("window", { localStorage: storage });
}

beforeEach(() => {
	withStorage(fakeStorage());
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe("the collapse preference", () => {
	it("is absent until the user chooses", () => {
		/** Null, not false. "Never touched it" and "chose expanded" are different: the first should
		 * follow the default if it ever changes, the second should not. */
		expect(loadCollapsed()).toBeNull();
	});

	it("round-trips both states", () => {
		storeCollapsed(true);
		expect(loadCollapsed()).toBe(true);

		storeCollapsed(false);
		expect(loadCollapsed()).toBe(false);
	});

	it("survives a browser that refuses to read storage", () => {
		/** Private-mode Safari and locked-down browsers throw on access. A preference that cannot be
		 * read is a preference that does not exist — the sidebar still has to work. */
		withStorage(
			fakeStorage({
				getItem: () => {
					throw new Error("access denied");
				},
			}),
		);

		expect(loadCollapsed()).toBeNull();
	});

	it("survives a browser that refuses to write it", () => {
		/** Losing the preference must never cost the click that set it. */
		withStorage(
			fakeStorage({
				setItem: () => {
					throw new Error("quota exceeded");
				},
			}),
		);

		expect(() => storeCollapsed(true)).not.toThrow();
	});

	it("treats a corrupted value as not collapsed rather than throwing", () => {
		storeCollapsed(true);
		window.localStorage.setItem("swarmkit.sidebar.collapsed", "garbage");

		expect(loadCollapsed()).toBe(false);
	});
});

describe("isActive", () => {
	it("matches the exact page", () => {
		expect(isActive("/jobs", "/jobs")).toBe(true);
	});

	it("matches a child route, so a detail page keeps its section highlighted", () => {
		expect(isActive("/jobs/history", "/jobs")).toBe(true);
	});

	it("does not match a sibling that merely shares a prefix", () => {
		/** Without the boundary, `/runs` would light up `/run` and two entries would look active at
		 * once — the nav would be lying about where you are. */
		expect(isActive("/runs", "/run")).toBe(false);
	});

	it("does not match an unrelated page", () => {
		expect(isActive("/audit", "/jobs")).toBe(false);
	});
});
