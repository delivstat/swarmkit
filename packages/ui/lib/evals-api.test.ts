/**
 * The eval calls against mocked serve routes (design/details/eval-on-serve.md §5): the paths and
 * methods each one hits, a 422 save coming back as errors for the form rather than a throw, and
 * a refused run (404/409/422/429) surfacing serve's reason.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

function route(status: number, body: unknown): ReturnType<typeof vi.fn> {
	const fetchMock = vi
		.fn()
		.mockResolvedValue(new Response(JSON.stringify(body), { status }));
	vi.stubGlobal("fetch", fetchMock);
	return fetchMock;
}

const call = (m: ReturnType<typeof vi.fn>) => ({
	url: m.mock.calls[0]?.[0] as string,
	init: (m.mock.calls[0]?.[1] ?? {}) as RequestInit,
});

afterEach(() => {
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe("eval sets", () => {
	it("lists sets and reads one, its YAML included", async () => {
		let m = route(200, []);
		await api.evalSets();
		expect(call(m).url).toBe("/api/evals");
		m = route(200, { yaml: "x" });
		await api.evalSetYaml("four");
		expect(call(m).url).toBe("/api/evals/four/yaml");
	});

	it("saves with PUT and a {yaml} body", async () => {
		const m = route(200, { valid: true, id: "four", case_count: 4 });
		const out = await api.saveEvalSet("four", "apiVersion: swarmkit/v1\n");
		expect(out).toEqual({ valid: true, id: "four", case_count: 4 });
		expect(call(m).init.method).toBe("PUT");
		expect(JSON.parse(String(call(m).init.body))).toEqual({
			yaml: "apiVersion: swarmkit/v1\n",
		});
	});

	it("returns a 422 as errors for the form, not as a thrown string", async () => {
		route(422, {
			valid: false,
			errors: [{ code: "target.unknown", message: "Unknown target 'nope'" }],
		});
		const out = await api.saveEvalSet("four", "x");
		expect(out.valid).toBe(false);
		if (!out.valid) expect(out.errors[0]?.code).toBe("target.unknown");
	});

	it("creates with POST /api/evals", async () => {
		const m = route(200, { valid: true, id: "new-one" });
		await api.createEvalSet("x");
		expect(call(m).url).toBe("/api/evals");
		expect(call(m).init.method).toBe("POST");
	});

	it("throws on a save refused for a reason other than validation", async () => {
		route(403, { detail: "admin required" });
		await expect(api.saveEvalSet("four", "x")).rejects.toThrow(
			"403: admin required",
		);
	});
});

describe("eval runs", () => {
	it("filters runs by set with ?set=", async () => {
		const m = route(200, []);
		await api.evalRuns("four", 5);
		expect(call(m).url).toBe("/evals/runs?limit=5&set=four");
	});

	it("starts, stops and resumes with POSTs to the run routes", async () => {
		let m = route(202, {
			eval_run_id: "r1",
			status: "running",
			eval_set_id: "four",
		});
		expect((await api.runEvalSet("four")).eval_run_id).toBe("r1");
		expect(call(m)).toMatchObject({
			url: "/evals/four/run",
			init: { method: "POST" },
		});
		m = route(200, { eval_run_id: "r1", stop_requested: true });
		await api.stopEvalRun("r1");
		expect(call(m).url).toBe("/evals/runs/r1/stop");
		m = route(202, {
			eval_run_id: "r1",
			status: "running",
			eval_set_id: "four",
		});
		await api.resumeEvalRun("r1");
		expect(call(m).url).toBe("/evals/runs/r1/resume");
	});

	it.each([
		[404, "No eval set 'gone'"],
		[409, "Eval run 'r1' is succeeded, not interrupted or stopped"],
		[422, "Unknown target 'nope'"],
		[429, "Max concurrent jobs reached. Try again later."],
	])("surfaces serve's reason for a %i", async (status, detail) => {
		route(status, { detail });
		await expect(api.runEvalSet("gone")).rejects.toThrow(
			`${status}: ${detail}`,
		);
	});
});
