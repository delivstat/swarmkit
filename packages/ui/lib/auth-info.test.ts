import { afterEach, describe, expect, it, vi } from "vitest";
import { fetchAuthInfo } from "./auth-info";

function mockFetch(body: unknown, status = 200): void {
	vi.stubGlobal(
		"fetch",
		vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status })),
	);
}

describe("fetchAuthInfo", () => {
	afterEach(() => vi.restoreAllMocks());

	it("reports mode none", async () => {
		mockFetch({ mode: "none" });
		expect(await fetchAuthInfo()).toEqual({ mode: "none" });
	});

	it("reports mode api_key", async () => {
		mockFetch({ mode: "api_key" });
		expect(await fetchAuthInfo()).toEqual({ mode: "api_key" });
	});

	it("reports jwt with the advertised issuer + audience", async () => {
		mockFetch({
			mode: "jwt",
			oidc: { issuer: "https://idp", audience: "swarmkit" },
		});
		expect(await fetchAuthInfo()).toEqual({
			mode: "jwt",
			oidc: { issuer: "https://idp", audience: "swarmkit" },
		});
	});

	it("falls back to none on a network error (degrade to open, not locked out)", async () => {
		vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
		expect(await fetchAuthInfo()).toEqual({ mode: "none" });
	});

	it("falls back to none on a non-ok response", async () => {
		mockFetch({}, 500);
		expect(await fetchAuthInfo()).toEqual({ mode: "none" });
	});
});
