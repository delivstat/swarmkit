/**
 * Capturing recorded runs as eval cases from the portal (design/details/eval-capture.md §5): the
 * body the view builds, the routes each call hits, a 422 coming back as a reason rather than a
 * throw, and the reasons shown for a skipped run.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CaptureProposalView } from "@/components/evals";
import { api } from "./api";
import {
	type EvalCaptureProposal,
	captureHref,
	captureReason,
	captureRequest,
} from "./evals";

function route(status: number, body: unknown): ReturnType<typeof vi.fn> {
	const fetchMock = vi
		.fn()
		.mockResolvedValue(new Response(JSON.stringify(body), { status }));
	vi.stubGlobal("fetch", fetchMock);
	return fetchMock;
}

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

const FIELDS = {
	into: " triage-regressions ",
	runIds: [] as string[],
	topology: "triage",
	since: "7d",
	limit: "50",
	redact: "DC-\\d+\n\n  acct-\\d+  \n",
	usedSkills: true,
};

const PROPOSAL: EvalCaptureProposal = {
	set_id: "triage-regressions",
	target: "triage",
	created: true,
	added: ["run-2e4a45a8", "run-063f9e85"],
	skipped: [
		{ run_id: "6f944283-aaaa", reason: "duplicate" },
		{
			run_id: "9a1b2c3d-bbbb",
			reason: "output-too-long",
			detail: "28,113 characters",
		},
	],
	flagged: [
		{ run_id: "77aa88bb-cccc", reason: "smoke-only", detail: "the run failed" },
	],
	yaml: "apiVersion: swarmkit/v1\nkind: EvalSet\n",
	written: false,
};

describe("the capture body", () => {
	it("selects a topology with its filters, trims the set id and keeps one pattern per line", () => {
		expect(captureRequest(FIELDS)).toEqual({
			into: "triage-regressions",
			topology: "triage",
			since: "7d",
			limit: 50,
			redact: ["DC-\\d+", "acct-\\d+"],
		});
	});

	it("named runs ignore the topology filters", () => {
		expect(captureRequest({ ...FIELDS, runIds: ["a1", "b2"] })).toEqual({
			into: "triage-regressions",
			run_ids: ["a1", "b2"],
			redact: ["DC-\\d+", "acct-\\d+"],
		});
	});

	it("sends used_skills only when it is turned off, and leaves blanks out", () => {
		const body = captureRequest({
			...FIELDS,
			since: "",
			limit: "",
			redact: "",
			usedSkills: false,
		});
		expect(body).toEqual({
			into: "triage-regressions",
			topology: "triage",
			used_skills: false,
		});
	});
});

describe("the capture routes", () => {
	it("previews with POST /evals/capture and writes with POST /api/evals/capture", async () => {
		let m = route(200, PROPOSAL);
		const preview = await api.previewEvalCapture({
			into: "x",
			topology: "triage",
		});
		expect(m.mock.calls[0]?.[0]).toBe("/evals/capture");
		expect((m.mock.calls[0]?.[1] as RequestInit).method).toBe("POST");
		expect(preview).toEqual({ ok: true, proposal: PROPOSAL });

		m = route(200, { ...PROPOSAL, written: true });
		const written = await api.writeEvalCapture({
			into: "x",
			topology: "triage",
		});
		expect(m.mock.calls[0]?.[0]).toBe("/api/evals/capture");
		expect(written.ok && written.proposal.written).toBe(true);
	});

	it("a 422 is a reason to show, not a throw", async () => {
		route(422, {
			code: "selection.mixed",
			detail: "these runs are of 2 topologies",
		});
		const out = await api.previewEvalCapture({
			into: "x",
			run_ids: ["a", "b"],
		});
		expect(out).toEqual({
			ok: false,
			code: "selection.mixed",
			detail: "these runs are of 2 topologies",
		});
	});

	it("anything else throws serve's reason", async () => {
		route(403, { detail: "Insufficient scope: requires serve:admin" });
		await expect(api.writeEvalCapture({ into: "x" })).rejects.toThrow(
			/403: Insufficient scope/,
		);
	});
});

describe("the capture view", () => {
	it("links into a set or from named runs", () => {
		expect(captureHref()).toBe("/evals/?capture=1");
		expect(captureHref({ set: "four" })).toBe("/evals/?capture=1&set=four");
		expect(captureHref({ runs: ["a1", "b2"] })).toBe(
			"/evals/?capture=1&runs=a1%2Cb2",
		);
	});

	it("says why a run was skipped, with serve's detail", () => {
		expect(captureReason({ run_id: "x", reason: "duplicate" })).toMatch(
			/same input/,
		);
		expect(
			captureReason({
				run_id: "x",
				reason: "output-too-long",
				detail: "28,113 characters",
			}),
		).toMatch(/never truncated.*28,113 characters/);
		expect(captureReason({ run_id: "x", reason: "something-new" })).toBe(
			"something-new",
		);
	});

	it("shows the cases to add, the flagged and skipped runs with reasons, and the file", () => {
		const html = renderToStaticMarkup(
			<CaptureProposalView proposal={PROPOSAL} />,
		);
		expect(html).toContain("2 cases to add to a new set");
		expect(html).toContain("+ run-2e4a45a8");
		expect(html).toMatch(
			/data-capture="flagged"[\s\S]*77aa88bb[\s\S]*re-runs the input/,
		);
		expect(html).toContain('data-reason="duplicate"');
		expect(html).toContain("over 20,000 characters");
		expect(html).toContain("apiVersion: swarmkit/v1");
		expect(html).not.toContain(">written<");
	});
});
