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

/**
 * `/auth-info` exists so a client can render the right login gate before it holds a token. The
 * client id is part of what a client needs to do that — see the note in oidc-config.test.ts for why
 * it cannot come from the build.
 */
describe("oidc discovery carries the browser's client registration", () => {
	it("passes client_id and scope through from the server", async () => {
		vi.stubGlobal(
			"fetch",
			vi.fn(async () => ({
				ok: true,
				json: async () => ({
					mode: "jwt",
					oidc: {
						issuer: "https://idp.example",
						audience: "swarmkit",
						client_id: "swarmkit-portal",
						scope: "openid profile email",
					},
				}),
			})),
		);
		const info = await fetchAuthInfo();
		expect(info.oidc?.client_id).toBe("swarmkit-portal");
		expect(info.oidc?.scope).toBe("openid profile email");
	});

	it("tolerates an older serve that advertises neither", async () => {
		// A serve on an earlier version omits both keys. The client must still render its gate and
		// fall back, not crash on a missing field.
		vi.stubGlobal(
			"fetch",
			vi.fn(async () => ({
				ok: true,
				json: async () => ({
					mode: "jwt",
					oidc: { issuer: "https://idp.example", audience: "swarmkit" },
				}),
			})),
		);
		const info = await fetchAuthInfo();
		expect(info.mode).toBe("jwt");
		expect(info.oidc?.client_id).toBeUndefined();
	});
});
