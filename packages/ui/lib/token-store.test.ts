import { afterEach, describe, expect, it, vi } from "vitest";
import {
	getAccessToken,
	handleUnauthorized,
	setAccessToken,
	setUnauthorizedHandler,
} from "./token-store";

describe("token-store", () => {
	afterEach(() => {
		setAccessToken(null);
		setUnauthorizedHandler(null);
	});

	it("holds the current token", () => {
		expect(getAccessToken()).toBeNull();
		setAccessToken("tok");
		expect(getAccessToken()).toBe("tok");
	});

	it("invokes the registered 401 handler", () => {
		const handler = vi.fn();
		setUnauthorizedHandler(handler);
		handleUnauthorized();
		expect(handler).toHaveBeenCalledOnce();
	});

	it("is a no-op when no handler is registered", () => {
		expect(() => handleUnauthorized()).not.toThrow();
	});
});
