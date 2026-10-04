// Thin client for SwarmKit's AG-UI protocol surface (`POST /api/ag-ui/run`, from #1020).
//
// The AG-UI protocol is served as SSE: one `data: {...}` frame per event. v1 of the server
// emits Lifecycle (RunStarted, RunFinished, RunError) + Messages (TextMessageStart/Content/End).
// This client decodes those frames and dispatches typed events.
//
// This module runs in both the Next.js API route (Node) and the browser (Edge-safe fetch).

export interface AgUiMessage {
	role: string;
	content: string;
}

export interface AgUiRunRequest {
	threadId?: string;
	runId?: string;
	messages: AgUiMessage[];
	context: Record<string, unknown>;
}

export type AgUiEvent =
	| { type: "RunStarted"; threadId: string; runId: string; input: unknown }
	| { type: "TextMessageStart"; messageId: string; role: string }
	| { type: "TextMessageContent"; messageId: string; delta: string }
	| { type: "TextMessageEnd"; messageId: string }
	| { type: "RunFinished"; runId: string; outcome: { type: string }; result?: unknown }
	| { type: "RunError"; runId: string; message: string; code: string }
	| { type: string; [key: string]: unknown };

export interface StreamHandlers {
	onEvent: (event: AgUiEvent) => void;
	signal?: AbortSignal;
}

export async function streamAgUiRun(
	baseUrl: string,
	body: AgUiRunRequest,
	handlers: StreamHandlers,
): Promise<void> {
	const res = await fetch(`${baseUrl}/api/ag-ui/run`, {
		method: "POST",
		headers: { "content-type": "application/json", accept: "text/event-stream" },
		body: JSON.stringify(body),
		signal: handlers.signal,
	});
	if (!res.ok || !res.body) {
		throw new Error(`ag-ui run failed: ${res.status} ${await res.text()}`);
	}
	const reader = res.body.getReader();
	const decoder = new TextDecoder();
	let buffer = "";
	while (true) {
		const { value, done } = await reader.read();
		if (done) break;
		buffer += decoder.decode(value, { stream: true });
		// SSE frames are separated by a blank line; data on `data: ...` lines.
		let sep = buffer.indexOf("\n\n");
		while (sep !== -1) {
			const frame = buffer.slice(0, sep).trim();
			buffer = buffer.slice(sep + 2);
			for (const line of frame.split("\n")) {
				if (!line.startsWith("data:")) continue;
				try {
					handlers.onEvent(JSON.parse(line.slice(5).trim()) as AgUiEvent);
				} catch {
					// Malformed frame: skip rather than fail the whole stream.
				}
			}
			sep = buffer.indexOf("\n\n");
		}
	}
}
