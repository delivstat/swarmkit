import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { JaegerLink } from "./jaeger-link";

describe("JaegerLink", () => {
	it("links to the instance's service-scoped Jaeger search", () => {
		render(
			<JaegerLink baseUrl="http://localhost:16686" service="sterling-oms" />,
		);
		const link = screen.getByRole("link", { name: /View in Jaeger/i });
		expect(link.getAttribute("href")).toBe(
			"http://localhost:16686/search?service=sterling-oms&lookback=1h&limit=20",
		);
		expect(link.getAttribute("target")).toBe("_blank");
	});

	it("renders nothing when Jaeger is not configured", () => {
		const { container } = render(
			<JaegerLink baseUrl="" service="sterling-oms" />,
		);
		expect(container.firstChild).toBeNull();
	});

	it("renders nothing when the service (workspace id) is unknown", () => {
		const { container } = render(
			<JaegerLink baseUrl="http://jaeger" service="" />,
		);
		expect(container.firstChild).toBeNull();
	});
});
