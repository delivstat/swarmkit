import { describe, expect, it } from "vitest";
import { oidcSettings } from "./oidc-config";

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
