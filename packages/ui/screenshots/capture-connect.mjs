/**
 * Capture the two connection surfaces side by side (per-caller-credential-delegation.md).
 *
 * The point of the pair is that they are *different pages reading different endpoints*, not one
 * page filtered by role:
 *
 *   /connections — the operator inventory. Every server, sink and remote agent, the credentials
 *                  they use, and an admin-only roster of who has connected what.
 *   /connect     — one person's own accounts. Which per-user connections exist and whether *they*
 *                  have connected them. It reads `GET /api/oauth/my-credentials`, which takes no
 *                  owner parameter and can name nobody else.
 *
 * Same approach as `capture.mjs`: run `next dev`, mock the serve API with fixtures through
 * Playwright route interception, write PNGs beside this file. No live backend.
 *
 *   node packages/ui/screenshots/capture-connect.mjs
 */
import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const HERE = dirname(fileURLToPath(import.meta.url));
const PORT = 3988;
const BASE = `http://127.0.0.1:${PORT}`;

/** What `GET /api/oauth/my-credentials` returns for the signed-in person. */
const MY_CREDENTIALS = {
	owner: "alice@example.com",
	credentials: [
		{
			credential_id: "google-calendar",
			source: "oauth",
			identity: "per-user",
			endpoint: "https://mcp.example.com/google",
			connected: true,
			scopes: ["calendar.events.read"],
			expires_at: Date.now() / 1000 + 3600,
			seconds_remaining: 3600,
			expired: false,
		},
		{
			credential_id: "mailbox",
			source: "oauth",
			identity: "per-user",
			endpoint: "https://mcp.example.com/mail",
			connected: false,
			scopes: [],
			expires_at: null,
			seconds_remaining: null,
			expired: null,
		},
		{
			credential_id: "github",
			source: "env",
			identity: "global",
			endpoint: null,
			connected: null,
			scopes: [],
			expires_at: null,
			seconds_remaining: null,
			expired: null,
		},
	],
};

/** The workspace the operator page renders: one shared connection, two personal ones. */
const CONFIG = {
	credentials: [
		{
			id: "github",
			source: "env",
			config: { env: "GITHUB_TOKEN" },
			resolves: true,
		},
		{
			id: "google-calendar",
			source: "oauth",
			config: {},
			resolves: true,
			identity: "per-user",
		},
		{
			id: "mailbox",
			source: "oauth",
			config: {},
			resolves: true,
			identity: "per-user",
		},
	],
	mcp_servers: [
		{
			id: "github",
			transport: "http",
			endpoint: "https://api.githubcopilot.com/mcp/",
			credentials_ref: "github",
			permission: "readonly",
		},
		{
			id: "google-calendar",
			transport: "http",
			endpoint: "https://mcp.example.com/google",
			credentials_ref: "google-calendar",
			permission: "cautious",
		},
		{
			id: "mailbox",
			transport: "http",
			endpoint: "https://mcp.example.com/mail",
			credentials_ref: "mailbox",
			permission: "cautious",
		},
	],
	events: [],
};

async function mock(page) {
	// BASE is a real origin here, but the app still fetches relative paths — so, as in capture.mjs,
	// a route must answer only fetch/xhr and let the document navigation through, or visiting
	// /connect serves the fixture JSON instead of the page.
	const api = (data) => (r) =>
		r.request().resourceType() === "document"
			? r.continue()
			: r.fulfill({
					status: 200,
					contentType: "application/json",
					body: JSON.stringify(data),
				});
	await page.route(/\/api\/oauth\/my-credentials$/, api(MY_CREDENTIALS));
	await page.route(/\/api\/workspace\/config$/, api(CONFIG));
	// The operator inventory: deliberately shown with two owners, which is exactly what the
	// caller-scoped page must never disclose.
	await page.route(
		/\/api\/oauth\/credentials$/,
		api({
			credentials: [
				{
					credential_id: "google-calendar",
					owner: "alice@example.com",
					provider: "google",
					endpoint: "https://mcp.example.com/google",
					scopes: ["calendar.events.read"],
					expires_at: Date.now() / 1000 + 3600,
					seconds_remaining: 3600,
					expired: false,
					has_refresh_token: true,
					refreshed_at: null,
				},
				{
					credential_id: "google-calendar",
					owner: "bob@example.com",
					provider: "google",
					endpoint: "https://mcp.example.com/google",
					scopes: ["calendar.events.read"],
					expires_at: Date.now() / 1000 + 7200,
					seconds_remaining: 7200,
					expired: false,
					has_refresh_token: true,
					refreshed_at: null,
				},
			],
		}),
	);
	await page.route(/\/api\/a2a\/agents$/, api([]));
	await page.route(/\/(health|capabilities|auth-info|whoami)$/, api({}));
}

async function main() {
	const dev = spawn("pnpm", ["exec", "next", "dev", "-p", String(PORT)], {
		cwd: join(HERE, ".."),
		stdio: "inherit",
		env: { ...process.env },
	});
	try {
		for (let i = 0; i < 90; i++) {
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
			viewport: { width: 1280, height: 860 },
		});
		await mock(page);

		await page.goto(`${BASE}/connect`, { waitUntil: "networkidle" });
		await sleep(1200);
		await page.screenshot({ path: join(HERE, "connect-as-you.png") });

		await page.goto(`${BASE}/connections`, { waitUntil: "networkidle" });
		await sleep(1200);
		await page.screenshot({ path: join(HERE, "connections-operator.png") });

		await browser.close();
		console.log("wrote connect-as-you.png and connections-operator.png");
	} finally {
		dev.kill("SIGTERM");
	}
}

main().catch((err) => {
	console.error(err);
	process.exit(1);
});
