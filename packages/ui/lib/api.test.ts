import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import { setAccessToken, setUnauthorizedHandler } from "./token-store";

function stubFetch(status: number): ReturnType<typeof vi.fn> {
	const fetchMock = vi
		.fn()
		.mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status }));
	vi.stubGlobal("fetch", fetchMock);
	return fetchMock;
}

function headersOf(
	fetchMock: ReturnType<typeof vi.fn>,
): Record<string, string> {
	const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
	return (init.headers ?? {}) as Record<string, string>;
}

describe("api bearer wiring", () => {
	afterEach(() => {
		setAccessToken(null);
		setUnauthorizedHandler(null);
		vi.restoreAllMocks();
	});

	it("attaches Authorization when a token is set", async () => {
		const fetchMock = stubFetch(200);
		setAccessToken("tok-123");
		await api.health();
		expect(headersOf(fetchMock).Authorization).toBe("Bearer tok-123");
	});

	it("omits Authorization when there is no token", async () => {
		const fetchMock = stubFetch(200);
		await api.health();
		expect(headersOf(fetchMock).Authorization).toBeUndefined();
	});

	it("triggers the 401 handler when an authenticated request is rejected", async () => {
		stubFetch(401);
		const onUnauthorized = vi.fn();
		setUnauthorizedHandler(onUnauthorized);
		setAccessToken("tok");
		await expect(api.health()).rejects.toThrow();
		expect(onUnauthorized).toHaveBeenCalledOnce();
	});

	it("does NOT trigger re-auth on a 401 for an unauthenticated request", async () => {
		stubFetch(401);
		const onUnauthorized = vi.fn();
		setUnauthorizedHandler(onUnauthorized);
		await expect(api.health()).rejects.toThrow();
		expect(onUnauthorized).not.toHaveBeenCalled();
	});
});
