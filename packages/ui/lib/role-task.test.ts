import { runOf, stageOf } from "@/app/gates/page";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

// Slice 3 of design/details/pipeline-gate-approval-ui.md — the client half of resolving a
// role-task: the request shape (no identity in the body), the gate-detail read, and the gate-id
// split the inbox deep-link depends on.

function stubFetch(body: unknown = { ok: true }, status = 200) {
	const fetchMock = vi
		.fn()
		.mockResolvedValue(new Response(JSON.stringify(body), { status }));
	vi.stubGlobal("fetch", fetchMock);
	return fetchMock;
}

const urlOf = (m: ReturnType<typeof vi.fn>) => String(m.mock.calls[0]?.[0]);
const bodyOf = (m: ReturnType<typeof vi.fn>) =>
	JSON.parse(String((m.mock.calls[0]?.[1] as RequestInit).body));

describe("reviewResolve", () => {
	afterEach(() => vi.restoreAllMocks());

	it("posts only the outcome — the resolver is the session, never the body", async () => {
		const fetchMock = stubFetch();
		await api.reviewResolve("mpa-run-42:design-0-security-reviewer", "approve");
		expect(urlOf(fetchMock)).toBe(
			"/review/mpa-run-42:design-0-security-reviewer/resolve",
		);
		expect(bodyOf(fetchMock)).toEqual({ outcome: "approve" });
		expect(bodyOf(fetchMock)).not.toHaveProperty("identity");
	});

	it("sends reject as an outcome, not a different endpoint", async () => {
		const fetchMock = stubFetch();
		await api.reviewResolve("mpa-x-0-r", "reject");
		expect(urlOf(fetchMock)).toBe("/review/mpa-x-0-r/resolve");
		expect(bodyOf(fetchMock)).toEqual({ outcome: "reject" });
	});

	it("surfaces the server's refusal text so the reason reaches the approver", async () => {
		stubFetch("alice is not a member of role release-manager", 403);
		await expect(api.reviewResolve("mpa-x-0-r", "approve")).rejects.toThrow(
			/not a member of role release-manager/,
		);
	});
});

describe("reviewPending filters", () => {
	afterEach(() => vi.restoreAllMocks());

	it("requests the bare queue when unfiltered", async () => {
		const fetchMock = stubFetch([]);
		await api.reviewPending();
		expect(urlOf(fetchMock)).toBe("/review");
	});

	it("passes kind and gate_id through", async () => {
		const fetchMock = stubFetch([]);
		await api.reviewPending({ kind: "role_task", gate_id: "run-42:design" });
		expect(urlOf(fetchMock)).toBe(
			"/review?kind=role_task&gate_id=run-42%3Adesign",
		);
	});
});

describe("gateStatus", () => {
	afterEach(() => vi.restoreAllMocks());

	it("encodes both path segments", async () => {
		const fetchMock = stubFetch({ items: [] });
		await api.gateStatus("run 42", "design/stage");
		expect(urlOf(fetchMock)).toBe(
			"/pipelines/gate-status/run%2042/design%2Fstage",
		);
	});
});

describe("gate id split", () => {
	it("splits a gate id into its run and stage", () => {
		expect(runOf("run-42:design")).toBe("run-42");
		expect(stageOf("run-42:design")).toBe("design");
	});

	it("splits on the LAST colon so a correlation id containing one still resolves", () => {
		expect(runOf("tenant:run-42:design")).toBe("tenant:run-42");
		expect(stageOf("tenant:run-42:design")).toBe("design");
	});

	it("degrades safely on a malformed gate id", () => {
		expect(runOf("nocolon")).toBe("nocolon");
		expect(stageOf("nocolon")).toBe("");
	});
});
