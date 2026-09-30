// Freeform-lane invocation: POST to runtime /api/run for the handle-item topology, streaming
// SSE progress + result. Design §Two data-fetch lanes: DOT proxies through the runtime; the
// runtime resolves credentials and dispatches to the LLM + MCP tools.

import { type SwarmKitClient, SwarmKitError } from "./swarmkit-client";

export interface RunHandleInput {
	itemId: string;
	suggestedAction: string;
	userIntent?: string;
	topology: string;
}

// Adds a startRun method by re-invoking the runtime with a POST that returns SSE.
// Kept out of swarmkit-client.ts so the client stays a plain HTTP wrapper — this file
// deliberately handles the streaming shape.
export async function startHandleRun(
	client: SwarmKitClient,
	input: RunHandleInput,
): Promise<Response> {
	const baseUrl = (client as unknown as { opts: { baseUrl: string } }).opts
		.baseUrl;
	const token = (client as unknown as { opts: { token: string } }).opts.token;
	const owner = (client as unknown as { opts: { owner: string } }).opts.owner;
	const res = await fetch(new URL("/api/run", baseUrl), {
		method: "POST",
		headers: {
			Authorization: `Bearer ${token}`,
			"X-Owner": owner,
			"Content-Type": "application/json",
			Accept: "text/event-stream",
		},
		body: JSON.stringify({
			topology_id: input.topology,
			input: {
				item_id: input.itemId,
				suggested_action: input.suggestedAction,
				user_intent: input.userIntent ?? null,
			},
		}),
	});
	if (!res.ok) {
		const txt = await res.text().catch(() => "");
		throw new SwarmKitError(
			`runtime /api/run failed: ${res.status} ${txt}`,
			res.status,
		);
	}
	return res;
}
