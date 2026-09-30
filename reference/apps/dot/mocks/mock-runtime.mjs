#!/usr/bin/env node
// Mock SwarmKit runtime for standalone DOT dev. Speaks the subset of the runtime API DOT uses:
//   POST /api/mcp/{server_id}/invoke — the fast-lane MCP invocation (v1.260.0).
//   GET  /health — trivial liveness.
//
// Design note Q4 default (b): a small mock runtime under mocks/ lets the app run without the
// real SwarmKit runtime installed. Do NOT use in production — no auth, no audit.
//
// Start with:  node mocks/mock-runtime.mjs                 (default port 8000)
// Override:    MOCK_RUNTIME_PORT=8010 node mocks/mock-runtime.mjs

import { createServer } from "node:http";

const PORT = Number(process.env.MOCK_RUNTIME_PORT ?? 8000);

const now = () => new Date();
const iso = (d) => d.toISOString();
const offsetMin = (mins) => iso(new Date(now().getTime() + mins * 60_000));

const FIXTURES = {
	gmail: {
		search_threads: () => ({
			threads: [
				{
					id: "t-oem-pilot",
					subject: "Re: DOT app pilot — cost breakdown",
					snippet:
						"Thanks for last week's numbers. Two of us are trying to close a Q4 spend estimate. Could you share…",
					from: "amber@partner.io",
					receivedAt: offsetMin(-125),
					labels: ["INBOX", "UNREAD"],
				},
				{
					id: "t-invoice",
					subject: "Invoice #2384 approved — auto-charge on Oct 3",
					snippet: "Your invoice has been approved. No action required.",
					from: "billing@ledger.co",
					receivedAt: offsetMin(-350),
					labels: ["INBOX", "UNREAD", "CATEGORY_UPDATES"],
				},
				{
					id: "t-newsletter",
					subject: "Three papers on eval harnesses this week",
					snippet:
						"Overview of arxiv 2509.11223, 2509.11801, 2509.12099 — recap and takeaways for eval infra teams…",
					from: "newsletter@aihouse.dev",
					receivedAt: offsetMin(-540),
					labels: ["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"],
				},
			],
		}),
	},
	"google-calendar": {
		list_events: () => ({
			events: [
				{
					id: "e-oem-sync",
					summary: "Sync with the Minder OEM partner",
					description:
						"Follow-up on last week's demo. Bring the reliability numbers and the pricing options.",
					start: offsetMin(165),
					end: offsetMin(195),
					attendees: 4,
				},
				{
					id: "e-q4-planning",
					summary: "Q4 planning",
					start: offsetMin(495),
					end: offsetMin(585),
					attendees: 12,
				},
			],
		}),
	},
};

const server = createServer((req, res) => {
	if (req.method === "GET" && req.url === "/health") {
		return json(res, 200, { ok: true, mock: true });
	}
	const invoke = /^\/api\/mcp\/([^/]+)\/invoke$/.exec(req.url ?? "");
	if (req.method === "POST" && invoke) {
		const serverId = decodeURIComponent(invoke[1]);
		return readBody(req).then((body) => {
			const { tool } = body;
			const impl = FIXTURES[serverId]?.[tool];
			if (!impl) {
				return json(res, 404, {
					error: `unknown_tool: ${serverId}.${tool}`,
				});
			}
			return json(res, 200, { result: impl(body.arguments ?? {}) });
		});
	}
	json(res, 404, { error: "not_found" });
});

function json(res, status, body) {
	res.writeHead(status, { "Content-Type": "application/json" });
	res.end(JSON.stringify(body));
}

async function readBody(req) {
	const chunks = [];
	for await (const c of req) chunks.push(c);
	try {
		return JSON.parse(Buffer.concat(chunks).toString("utf8"));
	} catch {
		return {};
	}
}

server.listen(PORT, () => {
	process.stderr.write(`mock-runtime listening on http://localhost:${PORT}\n`);
});
