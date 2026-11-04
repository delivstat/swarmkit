// Screenshot the Evals page (design/details/eval-on-serve.md §6) against a REAL serve, clicking
// through the flow rather than posing each view: list → review → an edit refused with the error on
// its field → a run in progress → stop after the case in flight → resume → the finished report
// naming the regression → the review flagging it.
//
//   pnpm --filter @swarmkit/ui build && cp -r packages/ui/out/. packages/webui/src/swarmkit_webui/_static/
//   uv run python packages/ui/demos/seed_eval_workspace.py 8097   # prints {"base","hang","break"}
//   SEED='<that JSON line>' node packages/ui/demos/screenshot_evals.mjs [outdir]

import { rmSync, writeFileSync } from "node:fs";
import { chromium } from "@playwright/test";

const out = process.argv[2] ?? ".";
const seed = JSON.parse(process.env.SEED ?? "{}");
const UI = seed.base ?? "http://127.0.0.1:8097";
const touch = (p) => writeFileSync(p, "");

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("console", (m) => {
	if (m.type() === "error") console.log("console error:", m.text());
});
const shot = async (name) => {
	await page.waitForTimeout(600);
	await page.screenshot({ path: `${out}/${name}.png`, fullPage: true });
	console.log(`wrote ${name}.png`);
};

// 1. The list: the set, its target and size, and the baseline run.
await page.goto(`${UI}/evals/`, { waitUntil: "networkidle" });
await page.waitForSelector("text=four");
await shot("evals-list");

// 2. A set's review: each case's input, expectations and recent results.
await page.click('a[href="/evals/?set=four"]');
await page.waitForSelector('[data-case="three"]');
await shot("evals-review");

// 3. Edit: point the set at a topology that does not exist, save — 422, shown on `target`.
await page.click("text=Edit");
await page.waitForSelector("text=cases");
await page.getByRole("button", { name: "YAML", exact: true }).click();
const yaml = page.locator("textarea").first();
await yaml.fill(
	(await yaml.inputValue()).replace(
		"target: change",
		"target: no-such-topology",
	),
);
await page.getByRole("button", { name: "Form", exact: true }).click();
await page.getByRole("button", { name: "Save" }).click();
await page.waitForSelector('[data-error-path="/target"]');
// The page scrolls inside its own container, so a full-page capture would not reach the error.
await page.getByText("Not saved").scrollIntoViewIfNeeded();
await shot("evals-edit-error");

// 4. Run it while case three hangs and case two has started answering wrongly.
touch(seed.hang);
touch(seed.break);
await page.goto(`${UI}/evals/?set=four`, { waitUntil: "networkidle" });
await page.getByRole("button", { name: "Run" }).click();
await page.waitForURL(/run=/);
await page.waitForSelector('[data-result="two"]', { timeout: 30000 });
await page.waitForSelector('[data-pending="three"]');
await shot("evals-run-in-progress");

// 5. Stop after the case in flight; let it finish; the run is stopped and resumable.
await page.getByRole("button", { name: "Stop after this case" }).click();
await page.waitForSelector("text=stopping after the case in flight");
rmSync(seed.hang);
await page.waitForSelector("text=Resume", { timeout: 30000 });
await shot("evals-run-stopped");

// 6. Resume: only case four runs; the finished report names the regression against the baseline.
rmSync(seed.break);
await page.getByRole("button", { name: /Resume/ }).click();
await page.waitForSelector('[data-diff="regressed"]', { timeout: 30000 });
await page.waitForSelector("text=Run again");
await shot("evals-report-regression");

// 7. Back on the review, case two is flagged at a glance.
await page.goto(`${UI}/evals/?set=four`, { waitUntil: "networkidle" });
await page.waitForSelector("text=regressed");
await shot("evals-review-regressed");

await browser.close();
