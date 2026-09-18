// Screenshot the portal for one tutorial level against a REAL serve on that level's example
// workspace (docs/site/tutorials, examples/tutorials/<level>). Pages are given on the command
// line as `route[:label]`; each becomes `<out>/<level>-<label>.png`.
//
//   SWARMKIT_PROVIDER=mock swarmkit serve examples/tutorials/01-hello-world --port 8125 \
//       --cors-origin http://127.0.0.1:3011
//   NEXT_PUBLIC_SWARMKIT_API=http://127.0.0.1:8125 pnpm --filter @swarmkit/ui dev -p 3011
//   pnpm --filter @swarmkit/ui demo:tutorial <level> <outdir> /jobs /job?id=@JOB@:job ...
//
// `@JOB@` in a route is replaced with the newest job id read from the API, so a script can run
// a topology first and then capture its detail page without knowing the id.

import { chromium } from "@playwright/test";

const [level, out, ...routes] = process.argv.slice(2);
const UI = process.env.UI_ORIGIN ?? "http://127.0.0.1:3011";
const API = process.env.API_ORIGIN ?? "http://127.0.0.1:8125";
if (!level || !out || routes.length === 0) {
	console.error(
		"usage: screenshot_tutorial.mjs <level> <outdir> route[:label] ...",
	);
	process.exit(2);
}

let job = "";
try {
	const jobs = await (await fetch(`${API}/jobs/history`)).json();
	job = jobs[0]?.job_id ?? "";
} catch {}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
// SHOOT_API_KEY logs the portal in the way the login gate would: the key is kept where
// `lib/auth-info.ts` reads it.
if (process.env.SHOOT_API_KEY) {
	await page.goto(UI, { waitUntil: "domcontentloaded" });
	await page.evaluate(
		(key) => window.localStorage.setItem("swarmkit.workspace.apiKey", key),
		process.env.SHOOT_API_KEY,
	);
}
page.on("console", (m) => {
	if (m.type() === "error") console.log("console error:", m.text());
});
const written = [];
for (const spec of routes) {
	// route[:label[:click=Button|Other]] — optional clicks after the page settles (a tab, a dialog).
	const [route, label, click] = spec.split(":");
	const path = route.replaceAll("@JOB@", job);
	const name = `${level}-${label ?? (path.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "") || "home")}.png`;
	await page.goto(`${UI}${path}`, { waitUntil: "networkidle" });
	await page.waitForTimeout(1500);
	if (click?.startsWith("click=")) {
		// `click=View|yaml` clicks each named button in turn — open a dialog, then pick its tab.
		for (const spec of click.slice(6).split("|")) {
			// `View@translate-text` clicks the View button in the row that mentions translate-text.
			const [name, within] = spec.split("@");
			const scope = within
				? page.locator("tr, .bg-card", { hasText: within }).first()
				: page;
			await scope
				.getByRole("button", { name: new RegExp(`^${name}$`, "i") })
				.first()
				.click({ force: true });
			await page.waitForTimeout(1500);
		}
	}
	// The portal's main pane is its own scroller (the window never scrolls), so `fullPage` cannot
	// capture a long page and a streaming job page may have scrolled itself to its latest event. A
	// label ending in `-full` gets a tall viewport instead; every capture scrolls the pane to the top.
	const full = (label ?? "").endsWith("-full");
	await page.setViewportSize({ width: 1440, height: full ? 2400 : 900 });
	await page.waitForTimeout(500);
	await page.evaluate(() => {
		window.scrollTo(0, 0);
		for (const el of document.querySelectorAll(
			"main, [data-scroll], .overflow-y-auto, .overflow-auto",
		))
			el.scrollTop = 0;
	});
	await page.waitForTimeout(300);
	await page.screenshot({ path: `${out}/${name}`, fullPage: false });
	written.push(name);
}
await browser.close();
console.log(`wrote ${written.join(", ")} to ${out}`);
