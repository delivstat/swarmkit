import { runOf } from "@/app/gates/page";
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
		// `comment` is additive and always sent (empty when not given); `identity` is the property
		// that must never appear — the resolver is the authenticated session.
		expect(bodyOf(fetchMock)).toEqual({ outcome: "approve", comment: "" });
		expect(bodyOf(fetchMock)).not.toHaveProperty("identity");
	});

	it("sends reject as an outcome, not a different endpoint", async () => {
		const fetchMock = stubFetch();
		await api.reviewResolve("mpa-x-0-r", "reject");
		expect(urlOf(fetchMock)).toBe("/review/mpa-x-0-r/resolve");
		expect(bodyOf(fetchMock)).toEqual({ outcome: "reject", comment: "" });
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

describe("which run a gate belongs to", () => {
	it("prefers the server's run_id over parsing the gate id", () => {
		// The whole point: the id has two shapes — `<correlation>:<stage>` for a pipeline stage and
		// `<run>:<agent>` for an in-node funnel gate — so a client that splits has to know which it
		// is holding, and got it wrong for one of them.
		expect(runOf({ gate_id: "wms-design:designer", run_id: "job-abc" })).toBe(
			"job-abc",
		);
	});

	it("falls back to splitting for items written before run_id existed", () => {
		expect(runOf({ gate_id: "run-42:design" })).toBe("run-42");
	});

	it("splits on the LAST colon so a correlation id containing one still resolves", () => {
		expect(runOf({ gate_id: "tenant:run-42:design" })).toBe("tenant:run-42");
	});

	it("degrades safely on a malformed gate id", () => {
		expect(runOf({ gate_id: "nocolon" })).toBe("nocolon");
	});
});

// ---- comments + the third outcome (human-decision-comments.md) --------------------------------

describe("decisions carry comments", () => {
	afterEach(() => vi.restoreAllMocks());

	it("sends the comment alongside the outcome", async () => {
		const fetchMock = stubFetch();
		await api.reviewResolve("mpa-x-0-r", "approve", "staging only for now");
		expect(bodyOf(fetchMock)).toEqual({
			outcome: "approve",
			comment: "staging only for now",
		});
	});

	it("supports changes-requested, which is neither approve nor reject", async () => {
		const fetchMock = stubFetch();
		await api.reviewResolve("mpa-x-0-r", "changes-requested", "add backoff");
		expect(bodyOf(fetchMock)).toEqual({
			outcome: "changes-requested",
			comment: "add backoff",
		});
	});

	it("still carries no identity — the resolver is the session", async () => {
		const fetchMock = stubFetch();
		await api.reviewResolve("mpa-x-0-r", "reject", "credentials in the diff");
		expect(bodyOf(fetchMock)).not.toHaveProperty("identity");
	});

	it("defaults the comment to empty rather than omitting it", async () => {
		const fetchMock = stubFetch();
		await api.reviewResolve("mpa-x-0-r", "approve");
		expect(bodyOf(fetchMock)).toEqual({ outcome: "approve", comment: "" });
	});

	it("passes a comment on a harness permission gate", async () => {
		const fetchMock = stubFetch();
		await api.reviewApprove("approval-1", "staging only");
		expect(urlOf(fetchMock)).toBe("/review/approval-1/approve");
		expect(bodyOf(fetchMock)).toEqual({ comment: "staging only" });
	});

	it("passes a comment alongside an input answer", async () => {
		const fetchMock = stubFetch();
		await api.reviewAnswer("input-1", "redis", "cap the pool at 20");
		expect(bodyOf(fetchMock)).toEqual({
			answer: "redis",
			comment: "cap the pool at 20",
		});
	});
});
