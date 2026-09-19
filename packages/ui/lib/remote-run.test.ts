import { describe, expect, it } from "vitest";
import { remoteRunLink } from "./remote-run";

describe("remoteRunLink", () => {
	it("builds a deep-link to the callee's job page from endpoint + remote_run_id", () => {
		const link = remoteRunLink({
			endpoint: "https://remote.example.com/a2a",
			remote_run_id: "abc123",
			card: "Review desk",
			cost_usd: 0.42,
		});
		expect(link).not.toBeNull();
		expect(link?.href).toBe("https://remote.example.com/job?id=abc123");
		expect(link?.label).toBe("open run on Review desk");
		expect(link?.costUsd).toBe(0.42);
	});

	it("strips a per-topology /a2a/<name> endpoint too", () => {
		const link = remoteRunLink({
			endpoint: "https://remote.example.com/a2a/triage",
			remote_run_id: "r1",
		});
		expect(link?.href).toBe("https://remote.example.com/job?id=r1");
	});

	it("returns null when the remote run id or endpoint is missing (non-SwarmKit remote)", () => {
		expect(remoteRunLink({ endpoint: "https://x/a2a" })).toBeNull();
		expect(remoteRunLink({ remote_run_id: "r1" })).toBeNull();
		expect(remoteRunLink({})).toBeNull();
	});

	it("returns null for a malformed endpoint", () => {
		expect(
			remoteRunLink({ endpoint: "not a url", remote_run_id: "r1" }),
		).toBeNull();
	});

	it("falls back to a generic label when no card name is given", () => {
		const link = remoteRunLink({
			endpoint: "https://x/a2a",
			remote_run_id: "r1",
		});
		expect(link?.label).toBe("open run on remote agent");
		expect(link?.costUsd).toBeNull();
	});
});
