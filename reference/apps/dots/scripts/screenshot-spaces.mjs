// Capture the Spaces user journey against a REAL swarmkit serve + the dots-app dev
// server, and ALSO capture the swarmkit portal view of the correlated jobs so a reviewer
// can see one id spanning two different Dots.
//
// Preconditions (handled by capture-spaces-screenshots.sh when run directly):
//   SWARMKIT_URL points at a live `swarmkit serve` (default http://127.0.0.1:8099)
//     — the demo workspace needs `authoring: { expose: true }` on workspace.yaml,
//       and at least a handle-item + morning-brief topology that returns short responses
//   dots-app running at APP_URL (default http://127.0.0.1:3509) with session env set

import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const APP = process.env.APP_URL ?? "http://127.0.0.1:3509";
const SWARMKIT = process.env.SWARMKIT_URL ?? "http://127.0.0.1:8099";
const USERNAME = process.env.DOTS_OWNER_USERNAME ?? "owner";
const PASSWORD = process.env.DOTS_OWNER_PASSWORD ?? "change-me";
const OUT = fileURLToPath(new URL("../docs/spaces-screenshots/", import.meta.url));
await mkdir(OUT, { recursive: true });

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
// Next dev's first-request compile on each route can take 10-40s; be patient.
page.setDefaultTimeout(90_000);
page.setDefaultNavigationTimeout(90_000);
page.on("console", (m) => {
	if (m.type() === "error") console.error("[console error]", m.text());
});
page.on("pageerror", (e) => console.error("[pageerror]", e.message));

async function shoot(name) {
	await page.screenshot({ path: `${OUT}${name}.png`, fullPage: true });
	console.log(`wrote docs/spaces-screenshots/${name}.png`);
}

async function step(label, fn) {
	console.log(`[step] ${label}`);
	try {
		await fn();
		console.log(`[step] ${label} ✓`);
	} catch (e) {
		console.error(`[step] ${label} ✗`, e.message);
		await page.screenshot({ path: `${OUT}err-${label}.png`, fullPage: true }).catch(() => {});
		throw e;
	}
}

async function waitSettled() {
	await page.waitForTimeout(2500);
}

// --- 1. login ----------------------------------------------------------------------
await page.goto(`${APP}/login`);
await page.waitForSelector("input[type=password]", { timeout: 20_000 });
await page.fill("input[name=username]", USERNAME);
await page.fill("input[name=password]", PASSWORD);
await page.click("button[type=submit]");
// Login POST sets the session cookie, then the client-side redirects to "/" or
// the "?next=" target. Give it a couple of seconds, then jump straight to /spaces.
await page.waitForTimeout(3_000);

// --- 2. empty state: sidebar has "Spaces" section --------------------------------
await page.goto(`${APP}/spaces`);
await page.waitForSelector("text=Spaces", { timeout: 20_000 });
await waitSettled();
await shoot("01-spaces-empty");

// --- 3. new space form ------------------------------------------------------------
await page.click("text=New Space");
await page.waitForURL(/\/spaces\/new/);
await page.waitForSelector("#id");
await page.fill("#id", "launch-q4-campaign");
await page.fill("#name", "Launch Q4 campaign");
await page.fill(
	"#description",
	"The campaign-launch Space — one place for every Dot I talk to about it.",
);
await waitSettled();
await shoot("02-new-space-form");

// --- 4. Space view, empty activity -----------------------------------------------
// POST + router.push can race with waitForURL under Next dev's HMR; sidestep by
// clicking, giving the POST a moment, then navigating directly.
await step("click create", () => page.click("button[type=submit]"));
await page.waitForTimeout(4_000);
await step("goto /spaces/launch-q4-campaign", () =>
	page.goto(`${APP}/spaces/launch-q4-campaign`, { waitUntil: "domcontentloaded" }),
);
await step("wait for correlation_id in header", () =>
	page.waitForSelector("text=correlation_id", { timeout: 90_000 }),
);
await waitSettled();
await shoot("03-space-view-empty");

// --- 5. drive real swarmkit runs via REST so the activity feed has rows ----------
// Chat UIs under CopilotKit are flaky to drive via Playwright selectors (textarea
// inside shadow-ish wrappers); the authoritative test is "does the Space activity
// feed show a run with the right correlation_id?" We satisfy that by starting runs
// server-side so the shots are deterministic.
async function startRun(topology) {
	const r = await fetch(`${SWARMKIT}/run/${topology}`, {
		method: "POST",
		headers: { "content-type": "application/json" },
		body: JSON.stringify({
			input: "(screenshot) a short note from the Space-view demo",
			correlation_id: "space:launch-q4-campaign",
			max_steps: 2,
		}),
	});
	if (!r.ok) console.error(`startRun ${topology}: ${r.status}`);
	return r.json();
}
await step("start Handle Item run in Space", () => startRun("handle-item"));
await page.waitForTimeout(20_000); // let it finish; model is small
await step("start Morning Brief run in Space", () => startRun("morning-brief"));
await page.waitForTimeout(20_000);

// --- 6. reload Space view; activity feed now shows two runs ----------------------
await step("reload Space view", () => page.reload({ waitUntil: "domcontentloaded" }));
await page.waitForSelector("text=handle-item", { timeout: 30_000 });
await waitSettled();
await shoot("04-space-activity-cross-dot");

// --- 7. switch Dot in the picker, same URL (chat picker demonstrates multi-Dot) --
await step("select Morning Brief in Dot picker", () =>
	page.selectOption("#dot-picker", "morning-brief"),
);
await waitSettled();
await shoot("05-space-dot-picker-morning-brief");

// --- 8. swarmkit portal: /jobs/history correlated via same id --------------------
// Hit the serve-hosted portal (same origin as swarmkit serve) in a second tab so we
// see what the runtime recorded. The portal is at $SWARMKIT_URL/.
const portal = await ctx.newPage();
portal.on("console", (m) => {
	if (m.type() === "error") console.error("[portal console error]", m.text());
});
await portal.goto(`${SWARMKIT}/jobs`);
await portal.waitForTimeout(3_000);
await portal.screenshot({ path: `${OUT}07-portal-jobs-all.png`, fullPage: true });
console.log("wrote docs/spaces-screenshots/07-portal-jobs-all.png");

await portal.goto(`${SWARMKIT}/jobs?correlation_id=space:launch-q4-campaign`);
await portal.waitForTimeout(3_000);
await portal.screenshot({
	path: `${OUT}08-portal-jobs-filtered.png`,
	fullPage: true,
});
console.log("wrote docs/spaces-screenshots/08-portal-jobs-filtered.png");

await browser.close();
console.log("done — 8 captures in docs/spaces-screenshots/");
