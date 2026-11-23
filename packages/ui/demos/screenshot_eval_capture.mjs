// Screenshot capturing recorded runs as eval cases (design/details/eval-capture.md §5) against a
// REAL serve, clicking through: the entry points on the Evals list, a set and a job; a preview from
// a topology's recent runs with the duplicate skipped and a warehouse code redacted; the write;
// the set it made; and a single run captured from its job page.
//
//   pnpm --filter @swarmkit/ui build && cp -r packages/ui/out/. packages/webui/src/swarmkit_webui/_static/
//   uv run python packages/ui/demos/seed_eval_workspace.py 8097   # prints {"base",...,"jobs"}
//   SEED='<that JSON line>' node packages/ui/demos/screenshot_eval_capture.mjs [outdir]

import { chromium } from "@playwright/test";

const out = process.argv[2] ?? ".";
const seed = JSON.parse(process.env.SEED ?? "{}");
const UI = seed.base ?? "http://127.0.0.1:8097";
const jobs = seed.jobs ?? [];

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

// 1. The Evals list has a "Capture runs" entry beside "New eval set".
await page.goto(`${UI}/evals/`, { waitUntil: "networkidle" });
await page.getByRole("link", { name: "Capture runs" }).waitFor();
await shot("capture-01-list-entry");

// 2. A new set from the topology's recent runs: pick the topology, add a redaction, preview.
await page.getByRole("link", { name: "Capture runs" }).click();
await page.waitForURL(/capture=1/);
await page.getByPlaceholder("triage-regressions").fill("change-regressions");
await page.locator("#capture-topology").click();
await page.getByRole("option", { name: "change" }).click();
await page.locator("textarea").fill("DC-\\d+");
await shot("capture-02-form");
await page.getByRole("button", { name: "Preview" }).click();
await page.getByText(/cases? to add to a new set/).waitFor();
await shot("capture-03-preview-skipped-and-redacted");

// 3. Write it, then open the set it made.
await page.getByRole("button", { name: /^Write \d+ case/ }).click();
await page.getByText("written", { exact: true }).waitFor();
await shot("capture-04-written");
await page.getByRole("link", { name: "Open the set" }).click();
await page.waitForURL(/set=change-regressions/);
await page.getByText(/cases against/).waitFor();
await shot("capture-05-the-new-set");

// 4. A set's own "Capture runs" captures into it: everything already there is a duplicate.
await page.getByRole("link", { name: "Capture runs" }).click();
await page.waitForURL(/capture=1&set=change-regressions/);
await page.getByRole("button", { name: "Preview" }).click();
await page.getByText(/to add to the set/).waitFor();
await shot("capture-06-into-a-set-nothing-new");

// 5. From a job's page: "Capture as eval case" for that one run.
if (jobs.length) {
	await page.goto(`${UI}/job/?id=${jobs[jobs.length - 1]}`, {
		waitUntil: "networkidle",
	});
	const entry = page.getByRole("link", { name: "Capture as eval case" });
	await entry.waitFor();
	// The portal scrolls inside its main panel, not the window: bring the Status card into view.
	await entry.scrollIntoViewIfNeeded();
	await shot("capture-07-job-entry");
	await page.getByRole("link", { name: "Capture as eval case" }).click();
	await page.waitForURL(/capture=1&runs=/);
	await page.getByPlaceholder("triage-regressions").fill("one-run");
	await page.getByRole("button", { name: "Preview" }).click();
	await page.getByText(/to add to a new set/).waitFor();
	await shot("capture-08-from-one-job");
}

await browser.close();
