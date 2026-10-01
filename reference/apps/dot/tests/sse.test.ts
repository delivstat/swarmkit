import { encodeEvent, readEvents } from "@/lib/sse";
import { describe, expect, it } from "vitest";

function toResponse(chunks: Uint8Array[]): Response {
	const stream = new ReadableStream({
		start(controller) {
			for (const c of chunks) controller.enqueue(c);
			controller.close();
		},
	});
	return new Response(stream, {
		headers: { "Content-Type": "text/event-stream" },
	});
}

describe("sse", () => {
	it("encodes an event with the SSE prefix and double newline terminator", () => {
		const bytes = encodeEvent({ type: "progress", message: "hi" });
		const text = new TextDecoder().decode(bytes);
		expect(text).toBe('data: {"type":"progress","message":"hi"}\n\n');
	});

	it("readEvents parses multi-frame streams", async () => {
		const res = toResponse([
			encodeEvent({ type: "progress", message: "one" }),
			encodeEvent({ type: "progress", message: "two" }),
			encodeEvent({
				type: "result",
				artefact: { kind: "acknowledgement", summary: "done" },
			}),
		]);
		const events: unknown[] = [];
		for await (const e of readEvents(res)) events.push(e);
		expect(events).toEqual([
			{ type: "progress", message: "one" },
			{ type: "progress", message: "two" },
			{
				type: "result",
				artefact: { kind: "acknowledgement", summary: "done" },
			},
		]);
	});

	it("readEvents survives a chunk split mid-frame", async () => {
		const full = new TextDecoder().decode(
			encodeEvent({ type: "progress", message: "hello" }),
		);
		const split = new TextEncoder();
		const res = toResponse([
			split.encode(full.slice(0, 10)),
			split.encode(full.slice(10)),
		]);
		const events: unknown[] = [];
		for await (const e of readEvents(res)) events.push(e);
		expect(events).toEqual([{ type: "progress", message: "hello" }]);
	});
});
