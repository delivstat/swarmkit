// Client wrapper for POST /api/items/{id}/{handle,chat}. Streams SSE events into a callback so
// the ResultPanel can show progress before the final artefact lands.

import type { HandleRunEvent } from "./artefacts";
import { readEvents } from "./sse";

export interface HandleRequest {
	suggestedAction: string;
	userIntent?: string;
}

export async function handleItem(
	id: string,
	body: HandleRequest,
	onEvent: (event: HandleRunEvent) => void,
	endpoint: "handle" | "chat" = "handle",
): Promise<void> {
	const res = await fetch(`/api/items/${encodeURIComponent(id)}/${endpoint}`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(body),
	});
	if (!res.ok) {
		const txt = await res.text().catch(() => "");
		onEvent({ type: "error", detail: txt || `HTTP ${res.status}` });
		return;
	}
	for await (const event of readEvents<HandleRunEvent>(res)) {
		onEvent(event);
	}
}
