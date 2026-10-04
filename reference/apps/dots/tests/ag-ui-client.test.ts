import { type AgUiEvent, streamAgUiRun } from "@/lib/ag-ui-client";
import { describe, expect, it, vi } from "vitest";

// SSE decoder must split on blank-line frames and skip malformed frames rather than crash.
// Pinning this because a bad frame in the middle of a stream (upstream bug, intermediary
// mangling) should degrade one event, not the whole chat turn.

function sseResponse(frames: string[]): Response {
	const body = frames.map((f) => `${f}\n\n`).join("");
	return new Response(body, {
		status: 200,
		headers: { "content-type": "text/event-stream" },
	});
}

describe("streamAgUiRun", () => {
	it("decodes RunStarted → Content* → RunFinished in order", async () => {
		vi.stubGlobal(
			"fetch",
			vi
				.fn()
				.mockResolvedValue(
					sseResponse([
						'data: {"type":"RunStarted","threadId":"t","runId":"r","input":{}}',
						'data: {"type":"TextMessageContent","messageId":"m","delta":"hi "}',
						'data: {"type":"TextMessageContent","messageId":"m","delta":"there"}',
						'data: {"type":"RunFinished","runId":"r","outcome":{"type":"success"}}',
					]),
				),
		);
		const events: AgUiEvent[] = [];
		await streamAgUiRun(
			"http://x",
			{ messages: [{ role: "user", content: "hi" }], context: { topology: "t" } },
			{ onEvent: (e) => events.push(e) },
		);
		expect(events.map((e) => e.type)).toEqual([
			"RunStarted",
			"TextMessageContent",
			"TextMessageContent",
			"RunFinished",
		]);
		const deltas = events
			.filter(
				(e): e is Extract<AgUiEvent, { type: "TextMessageContent" }> =>
					e.type === "TextMessageContent",
			)
			.map((e) => e.delta)
			.join("");
		expect(deltas).toBe("hi there");
	});

	it("skips malformed frames rather than aborting", async () => {
		vi.stubGlobal(
			"fetch",
			vi
				.fn()
				.mockResolvedValue(
					sseResponse([
						'data: {"type":"RunStarted","threadId":"t","runId":"r","input":{}}',
						"data: {not valid json",
						'data: {"type":"RunFinished","runId":"r","outcome":{"type":"success"}}',
					]),
				),
		);
		const types: string[] = [];
		await streamAgUiRun(
			"http://x",
			{ messages: [], context: {} },
			{ onEvent: (e) => types.push(e.type) },
		);
		expect(types).toEqual(["RunStarted", "RunFinished"]);
	});
});
