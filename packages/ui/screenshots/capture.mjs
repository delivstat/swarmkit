/**
 * Capture portal screenshots for the queue-stats strip (queue-observability.md) and the A2A trace
 * deep-link (a2a-federation.md). Runs `next dev`, mocks the serve API with fixtures via Playwright
 * route interception (BASE is "", so the app fetches relative paths), and writes PNGs beside this
 * file. No live backend needed — the fixtures are the shapes the endpoints return.
 *
 *   node packages/ui/screenshots/capture.mjs
 */
import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const HERE = dirname(fileURLToPath(import.meta.url));
const PORT = 3987;
const BASE = `http://127.0.0.1:${PORT}`;

const QUEUE_STATS = {
	queued: 6,
	running: 2,
	oldest_queued_age_seconds: 48.5,
	queue_wait_p50_seconds: 1.2,
	queue_wait_p95_seconds: 6.3,
	execution_p50_seconds: 0.64,
	execution_p95_seconds: 0.9,
	depth_by_topology: { typical: 4, "mcp-heavy": 2 },
	sample_size: 120,
};

const AUDIT = [
	{
		event_id: "e1",
		event_type: "skill.executed",
		agent_id: "leader",
		agent_role: "root",
		skill_id: "delegate",
		payload: { input: "review PR #42", output: "delegated to reviewer" },
		policy_decision: "allowed",
		run_id: "run-local-0001",
		timestamp: new Date().toISOString(),
		duration_ms: 820,
	},
	{
		event_id: "e2",
		event_type: "a2a.remote_usage",
		agent_id: "leader",
		agent_role: "root",
		skill_id: "reviewer_desk",
		payload: {
			skill_id: "reviewer_desk",
			card: "Review desk",
			endpoint: "https://reviewer.internal:8000/a2a",
			remote_run_id: "9f3c2a1b7d40",
			input_tokens: 1840,
			output_tokens: 512,
			cost_usd: 0.42,
			observability: { events: "/events", audit: "/audit" },
			source: "reported",
		},
		policy_decision: null,
		run_id: "run-local-0001",
		timestamp: new Date().toISOString(),
	},
];

async function mock(page) {
	const json = (data) => ({
		status: 200,
		contentType: "application/json",
		body: JSON.stringify(data),
	});
	await page.route("**/queue/stats", (r) => r.fulfill(json(QUEUE_STATS)));
	await page.route(/\/audit(\?.*)?$/, (r) => r.fulfill(json(AUDIT)));
	await page.route("**/jobs", (r) => r.fulfill(json([])));
	await page.route(/\/jobs\/history(\?.*)?$/, (r) => r.fulfill(json([])));
	// Anything else the layout probes (health, capabilities…) — answer empty so nothing hangs.
	await page.route(/\/(health|capabilities|auth-info)$/, (r) =>
		r.fulfill(json({})),
	);
}

async function main() {
	const dev = spawn("pnpm", ["exec", "next", "dev", "-p", String(PORT)], {
		cwd: join(HERE, ".."),
		stdio: "inherit",
		env: { ...process.env },
	});
	try {
		// Wait for next dev to answer.
		for (let i = 0; i < 60; i++) {
			try {
				const res = await fetch(BASE, { method: "HEAD" });
				if (res.ok || res.status < 500) break;
			} catch {
				/* not up yet */
			}
			await sleep(1000);
		}
		const browser = await chromium.launch({
			executablePath: process.env.PW_CHROME,
		});
		const page = await browser.newPage({
			viewport: { width: 1200, height: 800 },
		});
		await mock(page);

		await page.goto(`${BASE}/jobs`, { waitUntil: "networkidle" });
		await page.waitForTimeout(2500);
		await page.screenshot({ path: join(HERE, "queue-strip.png") });
		console.log("wrote queue-strip.png");

		await page.goto(`${BASE}/audit`, { waitUntil: "networkidle" });
		await page.waitForTimeout(2500);
		await page.screenshot({
			path: join(HERE, "a2a-deeplink.png"),
			fullPage: true,
		});
		console.log("wrote a2a-deeplink.png");

		await browser.close();
	} finally {
		dev.kill("SIGTERM");
	}
}

main().catch((e) => {
	console.error(e);
	process.exit(1);
});
