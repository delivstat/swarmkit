// Tiny stand-in for `swarmkit serve` that speaks the AG-UI protocol subset the dots app uses.
// Used in the standalone demo (no Python needed). Mirrors `POST /api/ag-ui/run` from PR #1020.
//
//   SWARMKIT_URL=http://127.0.0.1:4100 pnpm dev   # points the dots app at this instead

import { createServer } from "node:http";

const PORT = Number(process.env.PORT ?? 4100);

const CANNED = [
	"Pulling your brief…\n",
	"\n",
	"- Weekly review (needs reply by 5pm)\n",
	"- Budget sign-off (one line of context, you decide)\n",
	"- Interview feedback (overdue by one day)\n",
	"\n",
	"Which one do you want to handle first?",
];

function sse(payload) {
	return `data: ${JSON.stringify(payload)}\n\n`;
}

createServer(async (req, res) => {
	if (req.method !== "POST" || !req.url?.startsWith("/api/ag-ui/run")) {
		res.writeHead(404).end();
		return;
	}
	let raw = "";
	for await (const chunk of req) raw += chunk;
	let input;
	try {
		input = JSON.parse(raw);
	} catch {
		res.writeHead(400).end("bad json");
		return;
	}
	const threadId = input.threadId ?? `thr-${Date.now()}`;
	const runId = input.runId ?? `run-${Date.now()}`;
	const messageId = `msg-${Date.now()}`;

	res.writeHead(200, {
		"content-type": "text/event-stream",
		"cache-control": "no-cache, no-transform",
		connection: "keep-alive",
	});
	res.write(
		sse({
			type: "RunStarted",
			threadId,
			runId,
			input: { topology: input.context?.topology },
		}),
	);
	res.write(sse({ type: "TextMessageStart", messageId, role: "assistant" }));
	for (const delta of CANNED) {
		res.write(sse({ type: "TextMessageContent", messageId, delta }));
		await new Promise((r) => setTimeout(r, 120));
	}
	res.write(sse({ type: "TextMessageEnd", messageId }));
	res.write(sse({ type: "RunFinished", runId, outcome: { type: "success" } }));
	res.end();
}).listen(PORT, () => {
	console.log(`mock-runtime listening on http://127.0.0.1:${PORT}`);
});
