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

// 3. /dots/morning-brief — after login, greeting visible
await Promise.all([page.waitForURL(/\/dots\//), page.click("button[type=submit]")]);
await page.waitForSelector("text=Morning Brief");
await shoot(page, "03-dot-greeting");

// 4. /dots/morning-brief — user turn + assistant mid-stream
await page.fill("input[placeholder^='Ask']", "What's my brief?");
await page.click("button:has-text('Send')");
// mid-stream: wait for at least one assistant delta, screenshot before completion
await page.waitForSelector(".animate-pulse", { timeout: 5000 });
await shoot(page, "04-dot-streaming");

// 5. /dots/morning-brief — final state, no spinner
await page.waitForSelector(".animate-pulse", { state: "detached", timeout: 15000 });
await shoot(page, "05-dot-final");

await browser.close();

// Print markdown block for the PR body
console.log("## Screenshots\n");
for (const s of shots) {
	console.log(`### ${s.name.slice(3).replaceAll("-", " ")}\n`);
	console.log(`![${s.name}](${s.path})\n`);
}
