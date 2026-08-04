import { describe, expect, it } from "vitest";
import { missingClientId, oidcSettings } from "./oidc-config";

describe("oidcSettings", () => {
	it("uses the discovered issuer as authority + audience as an extra query param", () => {
		const s = oidcSettings({
			issuer: "https://idp.example",
			audience: "swarmkit",
		});
		expect(s.authority).toBe("https://idp.example");
		expect(s.extraQueryParams).toEqual({ audience: "swarmkit" });
		expect(s.scope).toContain("openid");
	});

	it("omits extraQueryParams when no audience is advertised", () => {
		const s = oidcSettings({ issuer: "https://idp.example", audience: "" });
		expect(s.extraQueryParams).toBeUndefined();
	});
});

/**
 * The published wheel could not be pointed at any identity provider.
 *
 * `client_id` came from NEXT_PUBLIC_OIDC_CLIENT_ID, which Next.js inlines at BUILD time. In the
 * published artifact it was never inlined — the bundle carried the literal source text
 * `NEXT_PUBLIC_OIDC_CLIENT_ID)?t:""`, a live property read against the browser `process` polyfill
 * whose `env` is `{}`. So it evaluated to "" on every load, `signinRedirect()` went out with
 * `client_id=`, and every IdP rejected it. There was no runtime escape hatch in a static export:
 * no env.js, no window.__ENV.
 *
 * The fix serves it from /auth-info, where `issuer` and `audience` already travel.
 */
describe("client_id and scope come from the server", () => {
	it("prefers the server-advertised client_id", () => {
		const s = oidcSettings({
			issuer: "https://idp.example",
			audience: "swarmkit",
			client_id: "swarmkit-portal",
		});
		expect(s.client_id).toBe("swarmkit-portal");
	});

	it("prefers the server-advertised scope", () => {
		const s = oidcSettings({
			issuer: "https://idp.example",
			audience: "swarmkit",
			scope: "openid profile email swarmkit",
		});
		expect(s.scope).toBe("openid profile email swarmkit");
	});

	it("falls back to the default scope when the server advertises none", () => {
		const s = oidcSettings({ issuer: "https://idp.example", audience: "" });
		expect(s.scope).toBe("openid profile email");
	});

	it("leaves client_id empty when nothing supplies one, rather than inventing a value", () => {
		// A wrong client_id fails at the IdP with an opaque error. Empty is detectable, and the
		// portal turns it into a message naming the setting to configure.
		const s = oidcSettings({ issuer: "https://idp.example", audience: "" });
		expect(s.client_id).toBe("");
	});
});

describe("missingClientId", () => {
	it("is true for an unconfigured client_id — the state the published wheel was always in", () => {
		expect(
			missingClientId(oidcSettings({ issuer: "https://i", audience: "" })),
		).toBe(true);
	});

	it("is true for whitespace, which would fail at the IdP just as opaquely", () => {
		expect(
			missingClientId(
				oidcSettings({ issuer: "https://i", audience: "", client_id: "   " }),
			),
		).toBe(true);
	});

	it("is false once the server advertises one", () => {
		expect(
			missingClientId(
				oidcSettings({
					issuer: "https://i",
					audience: "",
					client_id: "swarmkit-portal",
				}),
			),
		).toBe(false);
	});
});
