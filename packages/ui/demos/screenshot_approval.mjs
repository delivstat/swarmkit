// Screenshot the slice-3 approval surfaces against a REAL serve + a real parked run
// (design/details/pipeline-gate-approval-ui.md). It also *clicks* Approve, so the captures prove
// the round-trip, not just the layout.
//
//   uv run python packages/ui/demos/seed_parked_run.py
//   swarmkit serve /tmp/swarmkit-parked-demo --port 8099 --insecure \
//       --cors-origin http://127.0.0.1:3009
//   NEXT_PUBLIC_SWARMKIT_API=http://127.0.0.1:8099 pnpm --filter @swarmkit/ui dev -p 3009
//   pnpm --filter @swarmkit/ui demo:approval [outdir]

import { chromium } from "@playwright/test";

const out = process.argv[2] ?? ".";
const UI = process.env.UI_ORIGIN ?? "http://127.0.0.1:3009";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 820 } });
page.on("console", (m) => {
	if (m.type() === "error") console.log("console error:", m.text());
});

// 1. The deep link the Gates inbox emits lands on the parked stage.
await page.goto(`${UI}/runs?run=run-42&stage=greeter`, {
	waitUntil: "networkidle",
});
await page.waitForSelector("text=Approval", { timeout: 15000 });
await page.waitForTimeout(2000);
await page.screenshot({ path: `${out}/runs-approval.png` });

// 2. Resolve one role-task as the session identity; the gate stays parked (quorum: all).
const task = page.locator("li", { hasText: "security-reviewer" }).first();
const button = task.getByRole("button", { name: "Approve" });
if (await button.count()) {
	await button.click();
	await page.waitForTimeout(2000);
}
await page.screenshot({ path: `${out}/runs-approved.png` });

// 3. The inbox row — listed, not resolved here.
await page.goto(`${UI}/gates`, { waitUntil: "networkidle" });
await page.waitForTimeout(2000);
await page.screenshot({ path: `${out}/gates-inbox.png` });

await browser.close();
console.log(
	`wrote runs-approval.png, runs-approved.png, gates-inbox.png to ${out}`,
);
