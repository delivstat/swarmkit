import { describe, expect, it } from "vitest";

import { jaegerServiceUrl } from "./jaeger";

describe("jaegerServiceUrl", () => {
	it("builds a service-scoped Jaeger search URL", () => {
		expect(jaegerServiceUrl("http://localhost:16686", "sterling-oms")).toBe(
			"http://localhost:16686/search?service=sterling-oms&lookback=1h&limit=20",
		);
	});

	it("trims a trailing slash on the base URL", () => {
		expect(jaegerServiceUrl("http://jaeger/", "minder")).toBe(
			"http://jaeger/search?service=minder&lookback=1h&limit=20",
		);
	});

	it("url-encodes the service name", () => {
		expect(jaegerServiceUrl("http://j", "a b/c")).toBe(
			"http://j/search?service=a%20b%2Fc&lookback=1h&limit=20",
		);
	});

	it("returns null when Jaeger isn't configured or the service is unknown", () => {
		expect(jaegerServiceUrl("", "sterling-oms")).toBeNull();
		expect(jaegerServiceUrl("http://jaeger", "")).toBeNull();
	});
});
