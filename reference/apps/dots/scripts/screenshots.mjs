// Capture one screenshot per route × state pair for the UI-PR screenshot requirement.
// Writes to docs/screenshots/*.png and prints a markdown gallery block to stdout.
//
// Prereqs (handled by the sibling start-and-screenshot.sh wrapper):
//   SESSION_SECRET and DOTS_OWNER_PASSWORD_HASH exported
//   `pnpm mock-runtime` running on :4100
//   `SWARMKIT_URL=http://127.0.0.1:4100 pnpm dev` running on :3500

import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const BASE = process.env.APP_URL ?? "http://127.0.0.1:3500";
const USERNAME = process.env.DOTS_OWNER_USERNAME ?? "owner";
const PASSWORD = process.env.DOTS_OWNER_PASSWORD ?? "change-me";
const OUT = fileURLToPath(new URL("../docs/screenshots/", import.meta.url));

const shots = [];

async function shoot(page, name) {
	await mkdir(OUT, { recursive: true });
	const file = `${OUT}${name}.png`;
	await page.screenshot({ path: file, fullPage: true });
	shots.push({ name, path: `docs/screenshots/${name}.png` });
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const page = await ctx.newPage();

// 1. /login — empty
await page.goto(`${BASE}/login`);
await page.waitForSelector("input[type=password]");
await shoot(page, "01-login-empty");

// 2. /login — filled (just before submit)
await page.fill("input[name=username]", USERNAME);
await page.fill("input[name=password]", PASSWORD);
await shoot(page, "02-login-filled");

// 3. /dots/morning-brief — after login, CopilotChat initial greeting
await Promise.all([page.waitForURL(/\/dots\//), page.click("button[type=submit]")]);
await page.waitForSelector("text=Morning Brief");
await page.waitForTimeout(1500); // let CopilotKit settle
await shoot(page, "03-dot-greeting");

// 4. /dots/morning-brief — user turn + mid-stream
let composer = page.locator("textarea, [contenteditable=true]").first();
await composer.fill("What's my brief?");
await composer.press("Enter");
await page.waitForTimeout(400); // first delta
await shoot(page, "04-dot-streaming");

// 5. /dots/morning-brief — final state
await page.waitForTimeout(3000);
await shoot(page, "05-dot-final");

// 6. /dots/handle-item — second Dot proves multi-Dot routing + per-topology mock response
await page.click("text=Handle Item");
await page.waitForURL(/handle-item/);
await page.waitForTimeout(1500);
await shoot(page, "06-handle-item-greeting");

composer = page.locator("textarea, [contenteditable=true]").first();
await composer.fill("Reply to the budget sign-off");
await composer.press("Enter");
await page.waitForTimeout(3000);
await shoot(page, "07-handle-item-final");

// 8. /dots/github-triage — provider Dot
await page.click("text=GitHub Triage");
await page.waitForURL(/github-triage/);
await page.waitForTimeout(1500);
await shoot(page, "08-github-greeting");

composer = page.locator("textarea, [contenteditable=true]").first();
await composer.fill("Triage inbox");
await composer.press("Enter");
await page.waitForTimeout(3000);
await shoot(page, "09-github-final");

// 10. /connections — reusable OAuth status (empty state against mock runtime)
await page.goto(`${BASE}/connections`);
await page.waitForSelector("text=Connections");
await shoot(page, "10-connections");

// 11. /usage — token + cost summary (empty state against mock runtime)
await page.goto(`${BASE}/usage`);
await page.waitForSelector("text=Usage & cost");
await shoot(page, "11-usage");

// 12. /dots/author — the Author Dot's chat surface (now powered by the reusable
// reference/workspaces/author/ topology, not a deterministic frontend wizard).
await page.goto(`${BASE}/dots/author`);
await page.waitForSelector("text=Author");
await page.waitForTimeout(1500);
await shoot(page, "12-author-greeting");

// 13. user asks the Author for a new coworker — streaming assistant turn
composer = page.locator("textarea, [contenteditable=true]").first();
await composer.fill("I want a coworker that pulls weekly brief emails.");
await composer.press("Enter");
// Give Kimi + the AG-UI proxy time to stream back a response.
await page.waitForTimeout(20000);
await shoot(page, "13-author-conversation");

// 14. /dots/new — the raw YAML form stays reachable by URL but has no sidebar link
await page.goto(`${BASE}/dots/new`);
await page.waitForSelector("text=Add a Dot");
await shoot(page, "14-new-dot-advanced");

await browser.close();

// Print markdown block for the PR body
console.log("## Screenshots\n");
for (const s of shots) {
	console.log(`### ${s.name.slice(3).replaceAll("-", " ")}\n`);
	console.log(`![${s.name}](${s.path})\n`);
}
