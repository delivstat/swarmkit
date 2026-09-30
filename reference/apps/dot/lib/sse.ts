// Small SSE helpers used by the item-handle route (server) and the client fetch reader.

export function encodeEvent(data: unknown): Uint8Array {
	return new TextEncoder().encode(`data: ${JSON.stringify(data)}\n\n`);
}

// Client-side reader: consumes a fetch Response body as SSE and yields parsed JSON events.
export async function* readEvents<T>(res: Response): AsyncGenerator<T> {
	if (!res.body) return;
	const reader = res.body.getReader();
	const decoder = new TextDecoder();
	let buffer = "";
	while (true) {
		const { value, done } = await reader.read();
		if (done) break;
		buffer += decoder.decode(value, { stream: true });
		const frames = buffer.split("\n\n");
		buffer = frames.pop() ?? "";
		for (const frame of frames) {
			const line = frame.split("\n").find((l) => l.startsWith("data:"));
			if (!line) continue;
			try {
				yield JSON.parse(line.slice(5).trim()) as T;
			} catch {
				// discard malformed frame
			}
		}
	}
}
