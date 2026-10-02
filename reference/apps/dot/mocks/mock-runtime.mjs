#!/usr/bin/env node
// Mock SwarmKit runtime for standalone DOT dev. Speaks the subset of the runtime API DOT uses:
//   POST /api/mcp/{server_id}/invoke      — fast-lane MCP invocation (v1.260.0).
//   POST /api/run                         — SSE progress + result for handle-item.
//   GET  /api/oauth/my-credentials        — per-owner OAuth credentials (from #982).
//   POST /oauth/{provider}/start          — kick off OAuth; returns Google auth URL.
//   GET  /oauth/{provider}/mock-consent   — fake Google consent page (mock-only).
//   GET  /oauth/{provider}/mock-callback  — fake Google → runtime callback; 302 to return_to.
//   POST /api/oauth/credentials/{provider}— DELETE-alias for disconnect (accepts POST too).
//   DELETE /api/oauth/credentials/{provider} — disconnect.
//   GET  /health                          — liveness.
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

// In-memory OAuth store keyed by X-Owner header. Persists for the lifetime of the mock.
const OWNER_CREDS = new Map(); // owner -> { provider -> { expires_at, expired } }
const PENDING = new Map(); // state -> { returnTo, provider }

// Which skills need each credential (SwarmKit #1007). Mirrors what the real runtime builds
// from workspace archetypes + swarmkit-skills bundle manifests. Hard-coded here because the
// mock doesn't actually load a workspace.
const USED_BY = {
	gmail: [
		{ id: "search-threads", name: "Gmail — Search Threads" },
		{ id: "get-thread", name: "Gmail — Get Thread" },
		{ id: "get-message", name: "Gmail — Get Message" },
	],
	"google-calendar": [
		{ id: "list-events", name: "Calendar — List Events" },
		{ id: "list-calendars", name: "Calendar — List Calendars" },
		{ id: "get-event", name: "Calendar — Get Event" },
		{ id: "suggest-time", name: "Calendar — Suggest Time" },
	],
};

// Usage fixtures (design/details/dot-app.md §3.7). Deterministic pseudo-random daily series.
function hashInt(seed, i) {
	const x = Math.sin(seed * 9301 + i * 49297) * 0.5 + 0.5;
	return x;
}

function usageDaily(window) {
	const days = [];
	const start = Date.now() - (window - 1) * 86_400_000;
	for (let i = 0; i < window; i++) {
		const d = new Date(start + i * 86_400_000);
		const base = 0.12 + hashInt(1, i) * 0.9 + (i > window - 3 ? 0.3 : 0);
		const tokensIn = Math.round(2500 + hashInt(2, i) * 9500);
		const tokensOut = Math.round(900 + hashInt(3, i) * 3100);
		days.push({
			date: d.toISOString().slice(0, 10),
			tokensIn,
			tokensOut,
			costUsd: Math.round(base * 100) / 100,
		});
	}
	return { days };
}

function sumRange(window) {
	return usageDaily(window).days.reduce(
		(acc, d) => ({
			tokensIn: acc.tokensIn + d.tokensIn,
			tokensOut: acc.tokensOut + d.tokensOut,
			costUsd: Math.round((acc.costUsd + d.costUsd) * 100) / 100,
		}),
		{ tokensIn: 0, tokensOut: 0, costUsd: 0 },
	);
}

function usageSummary() {
	const todaySrc = usageDaily(1).days[0];
	const today = {
		tokensIn: todaySrc.tokensIn,
		tokensOut: todaySrc.tokensOut,
		costUsd: todaySrc.costUsd,
	};
	const now = new Date();
	const mtd = sumRange(now.getUTCDate());
	const last30 = sumRange(30);
	return { today, monthToDate: mtd, last30Days: last30 };
}

function usageBreakdown(by) {
	if (by === "topology") {
		return {
			by,
			rows: [
				{
					label: "morning-brief",
					tokensIn: 42000,
					tokensOut: 14000,
					costUsd: 7.84,
				},
				{
					label: "handle-item",
					tokensIn: 71000,
					tokensOut: 23500,
					costUsd: 12.3,
				},
			],
		};
	}
	return {
		by,
		rows: [
			{ label: "anthropic", tokensIn: 61000, tokensOut: 18200, costUsd: 11.4 },
			{ label: "openai", tokensIn: 32000, tokensOut: 11200, costUsd: 5.6 },
			{ label: "ollama", tokensIn: 20000, tokensOut: 8100, costUsd: 0 },
		],
	};
}

// Audit-log fixtures for the Activity page (design/details/dot-app.md §3.6).
const AUDIT_ENTRIES = [
	{
		run_id: "r-1",
		topology_id: "morning-brief",
		started_at: offsetMin(-20),
		elapsed_ms: 8400,
		status: "success",
		summary: "Morning brief · 5 items",
	},
	{
		run_id: "r-2",
		topology_id: "handle-item",
		started_at: offsetMin(-14),
		elapsed_ms: 1200,
		status: "success",
		summary: "handle-item · prep · OEM sync",
	},
	{
		run_id: "r-3",
		topology_id: "handle-item",
		started_at: offsetMin(-9),
		elapsed_ms: 2100,
		status: "success",
		summary: "handle-item · draft_reply · cost breakdown",
	},
	{
		run_id: "r-4",
		topology_id: "handle-item",
		started_at: offsetMin(-4),
		elapsed_ms: 640,
		status: "error",
		summary: "handle-item · archive · newsletter (500)",
	},
];

const AUDIT_DETAILS = {
	"r-1": {
		...AUDIT_ENTRIES[0],
		archetypes: [
			{ id: "context-aggregator", elapsed_ms: 3100, status: "success" },
			{ id: "item-ranker", elapsed_ms: 5300, status: "success" },
		],
		errors: [],
	},
	"r-2": {
		...AUDIT_ENTRIES[1],
		archetypes: [
			{ id: "meeting-prepper", elapsed_ms: 1200, status: "success" },
		],
		errors: [],
	},
	"r-3": {
		...AUDIT_ENTRIES[2],
		archetypes: [
			{ id: "conversation-retriever", elapsed_ms: 800, status: "success" },
			{ id: "email-drafter", elapsed_ms: 1300, status: "success" },
		],
		errors: [],
	},
	"r-4": {
		...AUDIT_ENTRIES[3],
		archetypes: [{ id: "email-drafter", elapsed_ms: 640, status: "error" }],
		errors: [
			{
				archetype: "email-drafter",
				message: "Gmail archive_thread: 500 Internal Server Error",
			},
		],
	},
};

// Workspace-config projection the Settings page reads/writes (design/details/dot-app.md §3.5).
const WORKSPACE_CONFIG = {
	triggers: {
		"morning-brief": { time: "07:30", timezone: "America/Los_Angeles" },
	},
	defaults: { model: { provider: "ollama", name: "llama3.1:8b" } },
	topologies: { "morning-brief": { input: { item_cap: 10 } } },
};

function mergeDeep(target, patch) {
	for (const [k, v] of Object.entries(patch ?? {})) {
		if (v && typeof v === "object" && !Array.isArray(v)) {
			if (!target[k] || typeof target[k] !== "object") target[k] = {};
			mergeDeep(target[k], v);
		} else {
			target[k] = v;
		}
	}
}

function ownerFrom(req) {
	return req.headers["x-owner"] ?? "owner";
}

const server = createServer((req, res) => {
	const url = new URL(req.url ?? "/", `http://${req.headers.host}`);
	const p = url.pathname;

	if (req.method === "GET" && p === "/health") {
		return json(res, 200, { ok: true, mock: true });
	}
	const invoke = /^\/api\/mcp\/([^/]+)\/invoke$/.exec(p);
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
	if (req.method === "POST" && p === "/api/run") {
		return readBody(req).then((body) => streamRun(res, body));
	}
	if (req.method === "GET" && p === "/api/oauth/my-credentials") {
		const owner = ownerFrom(req);
		const creds = Object.entries(OWNER_CREDS.get(owner) ?? {}).map(
			([provider, meta]) => ({
				provider,
				owner,
				expires_at: meta.expires_at,
				expired: meta.expired,
				// `used_by` mirrors the real runtime's shape (SwarmKit #1007). Fixtures below.
				used_by: USED_BY[provider] ?? [],
			}),
		);
		return json(res, 200, { credentials: creds });
	}
	const start = /^\/oauth\/([^/]+)\/start$/.exec(p);
	if (req.method === "POST" && start) {
		const provider = decodeURIComponent(start[1]);
		return readBody(req).then((body) => {
			const state = Math.random().toString(36).slice(2);
			PENDING.set(state, {
				returnTo: body.return_to,
				provider,
				owner: ownerFrom(req),
			});
			const authUrl = `http://localhost:${PORT}/oauth/${encodeURIComponent(provider)}/mock-consent?state=${state}`;
			return json(res, 200, { auth_url: authUrl });
		});
	}
	const consent = /^\/oauth\/([^/]+)\/mock-consent$/.exec(p);
	if (req.method === "GET" && consent) {
		const provider = decodeURIComponent(consent[1]);
		const state = url.searchParams.get("state") ?? "";
		return html(res, 200, renderConsent(provider, state));
	}
	const cb = /^\/oauth\/([^/]+)\/mock-callback$/.exec(p);
	if (req.method === "GET" && cb) {
		const state = url.searchParams.get("state") ?? "";
		const decision = url.searchParams.get("decision") ?? "allow";
		const pending = PENDING.get(state);
		PENDING.delete(state);
		if (!pending) return html(res, 400, "<h1>Missing or expired state.</h1>");
		if (decision === "allow") {
			const bucket = OWNER_CREDS.get(pending.owner) ?? {};
			bucket[pending.provider] = {
				expires_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
				expired: false,
			};
			OWNER_CREDS.set(pending.owner, bucket);
		}
		res.writeHead(302, { Location: pending.returnTo });
		return res.end();
	}
	if (req.method === "GET" && p === "/api/usage/summary") {
		return json(res, 200, usageSummary());
	}
	if (req.method === "GET" && p === "/api/usage/daily") {
		const window = Number(url.searchParams.get("window") ?? 30);
		return json(res, 200, usageDaily(window));
	}
	if (req.method === "GET" && p === "/api/usage/breakdown") {
		const by = url.searchParams.get("by") ?? "provider";
		return json(res, 200, usageBreakdown(by));
	}
	if (req.method === "GET" && p === "/audit") {
		const topologies = url.searchParams.getAll("topology");
		const filtered = topologies.length
			? AUDIT_ENTRIES.filter((e) => topologies.includes(e.topology_id))
			: AUDIT_ENTRIES;
		return json(res, 200, { entries: filtered, next_cursor: null });
	}
	const audit = /^\/audit\/([^/]+)$/.exec(p);
	if (req.method === "GET" && audit) {
		const runId = decodeURIComponent(audit[1]);
		const entry = AUDIT_DETAILS[runId];
		if (!entry) return json(res, 404, { error: "not_found" });
		return json(res, 200, entry);
	}
	if (req.method === "GET" && p === "/api/workspace-config") {
		return json(res, 200, WORKSPACE_CONFIG);
	}
	if (req.method === "PATCH" && p === "/api/workspace-config") {
		return readBody(req).then((patch) => {
			mergeDeep(WORKSPACE_CONFIG, patch);
			return json(res, 200, WORKSPACE_CONFIG);
		});
	}
	const disconnect = /^\/api\/oauth\/credentials\/([^/]+)$/.exec(p);
	if ((req.method === "DELETE" || req.method === "POST") && disconnect) {
		const provider = decodeURIComponent(disconnect[1]);
		const owner = ownerFrom(req);
		const bucket = OWNER_CREDS.get(owner);
		if (bucket) delete bucket[provider];
		return json(res, 200, { ok: true });
	}
	json(res, 404, { error: "not_found" });
});

function renderConsent(provider, state) {
	const allow = `/oauth/${encodeURIComponent(provider)}/mock-callback?state=${state}&decision=allow`;
	const deny = `/oauth/${encodeURIComponent(provider)}/mock-callback?state=${state}&decision=deny`;
	return `<!doctype html>
<html><head><title>Mock consent — ${provider}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:system-ui;background:#0f0f0f;color:#e5e5e5;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}main{max-width:420px;padding:24px;background:#171717;border:1px solid #262626;border-radius:12px}h1{font-size:18px;margin:0 0 8px}p{color:#a3a3a3;font-size:14px;line-height:1.4}a.btn{display:inline-block;margin-top:16px;margin-right:8px;padding:10px 14px;border-radius:8px;font-size:14px;text-decoration:none}a.allow{background:#22c55e;color:#052e14}a.deny{background:#262626;color:#e5e5e5;border:1px solid #404040}</style>
</head><body><main>
<h1>Mock Google — grant DOT access to your ${provider}?</h1>
<p>This is a stand-in for Google's consent screen so DOT can demo the connect flow without real OAuth credentials. In production the runtime redirects here to Google, then Google to the runtime callback, and finally the runtime 302s back to DOT.</p>
<a class="btn allow" href="${allow}">Allow</a>
<a class="btn deny" href="${deny}">Deny</a>
</main></body></html>`;
}

function html(res, status, body) {
	res.writeHead(status, { "Content-Type": "text/html; charset=utf-8" });
	res.end(body);
}

function json(res, status, body) {
	res.writeHead(status, { "Content-Type": "application/json" });
	res.end(JSON.stringify(body));
}

function sse(res, event) {
	res.write(`data: ${JSON.stringify(event)}\n\n`);
}

async function streamRun(res, body) {
	res.writeHead(200, {
		"Content-Type": "text/event-stream",
		"Cache-Control": "no-cache, no-transform",
		Connection: "keep-alive",
	});
	const input = body.input ?? {};
	const action = input.suggested_action ?? "unknown";
	const itemId = input.item_id ?? "unknown";
	const userIntent = input.user_intent ?? null;

	sse(res, { type: "progress", message: `Loading context for ${itemId}…` });
	await wait(300);
	sse(res, { type: "progress", message: `Running ${action}…` });
	await wait(500);
	sse(res, {
		type: "result",
		artefact: artefactFor(action, itemId, userIntent),
	});
	res.end();
}

function artefactFor(action, itemId, userIntent) {
	if (
		action === "draft_reply" ||
		(action === "chat" && /reply|write|draft/i.test(userIntent ?? ""))
	) {
		return {
			kind: "email_draft",
			to: "amber@partner.io",
			subject: "Re: DOT app pilot — cost breakdown",
			body: "Hi Amber,\n\nAttaching last month's totals. Happy to walk you through the shape on a quick call tomorrow if useful.\n\nSrijith",
		};
	}
	if (action === "prep" || action === "attend") {
		return {
			kind: "prep_note",
			title: "OEM sync — talking points",
			sections: [
				{
					heading: "Wins since last week",
					bullets: [
						"Detector latency now sub-45 ms on the appliance",
						"Face plugin at ArcFace parity in the field",
					],
				},
				{
					heading: "Open questions",
					bullets: [
						"Pricing tier for the 2-camera pack",
						"SLA on cold-start recovery",
					],
				},
			],
		};
	}
	if (action === "retrieve_context") {
		return {
			kind: "retrieved_thread",
			title: "Prior thread with Amber (Aug 12)",
			summary:
				"You shared the initial numbers and Amber asked for a written breakdown. Closed with a promise to send Q4.",
			links: [
				{
					label: "Open in Gmail",
					href: "https://mail.google.com/mail/u/0/#inbox",
				},
			],
		};
	}
	if (action === "archive") {
		return { kind: "acknowledgement", summary: `Archived ${itemId}.` };
	}
	if (action === "decline") {
		return {
			kind: "acknowledgement",
			summary: `Declined ${itemId} and notified attendees.`,
		};
	}
	return {
		kind: "acknowledgement",
		summary: `Chat response for "${userIntent ?? action}".`,
	};
}

function wait(ms) {
	return new Promise((r) => setTimeout(r, ms));
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
