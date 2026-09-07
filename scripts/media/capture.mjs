/**
 * Capture portal screenshots and a walkthrough video from a running `swarmkit serve`.
 *
 * The showcase assets were filmed before 1.189.0 and show a removed feature. Rather than
 * re-recording by hand every release, this drives the real portal with Playwright — so the images
 * in the docs are generated from the version in the tree, and go stale only when someone forgets
 * to run it rather than silently.
 *
 *   node scripts/media/capture.mjs --serve http://127.0.0.1:8140 --out docs/site/img/portal
 *
 * Requires a serve with a parked run, so the gate screens have something in them: an empty
 * inbox screenshot teaches nothing and looks like a broken product.
 */
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

const args = Object.fromEntries(
	process.argv.slice(2).reduce((acc, a, i, arr) => {
		if (a.startsWith("--")) acc.push([a.slice(2), arr[i + 1]]);
		return acc;
	}, []),
);
const BASE = args.serve ?? "http://127.0.0.1:8140";
const OUT = args.out ?? "docs/site/img/portal";
const VIDEO = args.video ?? join(OUT, "video");

// Wide enough that the sidebar and content both read at docs width; 2x so the images stay sharp
// when a reader zooms. Portal pages are responsive, so this is a choice not a constraint.
const VIEWPORT = { width: 1440, height: 900 };
const SCALE = 2;

/** Pages worth showing, and why each is in the set. */
const PAGES = [
	["dashboard", "/dashboard", "the workspace at a glance"],
	["topologies", "/topologies", "the workspace's topologies"],
	// The canvas lives behind Edit, and it is the picture that sells topology-as-data — a graph
	// rendered from the YAML rather than drawn by hand. Worth the extra route.
	["composer", "/composer?topology=release", "the topology's structure, read back from YAML"],
	// The graph view sits behind a tab, so this one needs a click. It is the picture that sells
	// topology-as-data, which is worth a special case in an otherwise uniform list.
	// Lowercase: the tab renders "Canvas" via CSS `capitalize`, which does not change the
	// accessible name the DOM actually exposes.
	["canvas", "/composer?topology=release", "the swarm as a graph", "canvas"],
	["gates", "/gates", "a run parked on a human decision"],
	["jobs", "/jobs", "run history with status and cost"],
	["skills", "/skills", "the capability catalogue"],
	["archetypes", "/archetypes", "reusable agent definitions"],
	["connections", "/connections", "servers, credentials and what resolves"],
	["audit", "/audit", "the append-only trail"],
];

async function settle(page) {
	// The portal polls; a screenshot taken mid-fetch catches a spinner. Wait for the network to
	// go quiet, then a beat for any transition to finish.
	await page.waitForLoadState("networkidle").catch(() => {});
	// The canvas lays out after its data arrives, so this waits longer than a list page needs.
	await page.waitForTimeout(1500);
}

/**
 * Refuse to photograph a page that is not there.
 *
 * The first run of this script saved the portal's 404 as `connections.png`, because the serve was
 * hosting a webui build from before that page existed. A screenshot captures whatever is on screen
 * with equal enthusiasm, so the check has to be explicit — a docs site illustrating a feature with
 * its own 404 is worse than having no screenshot at all.
 */
async function assertReal(page, name) {
	// VISIBILITY, not presence. The Next.js static export bundles the not-found markup into every
	// page, so searching the body text matches on pages that render perfectly well — the first
	// version of this check failed on `dashboard`.
	const notFound = page.getByText("This page could not be found", { exact: false });
	if (await notFound.isVisible().catch(() => false)) {
		throw new Error(
			`${name}: the portal returned 404. The served build is older than this page — ` +
				"rebuild the UI and stage it into packages/webui/src/swarmkit_webui/_static/.",
		);
	}
}

async function main() {
	mkdirSync(OUT, { recursive: true });
	mkdirSync(VIDEO, { recursive: true });

	const browser = await chromium.launch();

	// --- screenshots -------------------------------------------------------------------
	const shots = await browser.newContext({
		viewport: VIEWPORT,
		deviceScaleFactor: SCALE,
		colorScheme: "dark",
	});
	const page = await shots.newPage();
	for (const [name, path, why, tab] of PAGES) {
		await page.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded" });
		await settle(page);
		await assertReal(page, name);
		if (tab) {
			await page.getByRole("button", { name: tab, exact: true }).click();
			await page.waitForTimeout(1500);
		}
		await page.screenshot({ path: join(OUT, `${name}.png`) });
		console.log(`  ${name.padEnd(14)} ${why}`);
	}
	await shots.close();

	// --- video -------------------------------------------------------------------------
	// One continuous pass over the same pages, paced for a viewer rather than a test.
	const filmed = await browser.newContext({
		viewport: VIEWPORT,
		colorScheme: "dark",
		recordVideo: { dir: VIDEO, size: VIEWPORT },
	});
	const stage = await filmed.newPage();
	for (const [, path, , tab] of PAGES) {
		await stage.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded" });
		await settle(stage);
		if (tab) {
			await stage.getByRole("button", { name: tab, exact: true }).click().catch(() => {});
			await stage.waitForTimeout(1200);
		}
		await stage.waitForTimeout(1800);
	}
	await filmed.close(); // the video is only written on close
	await browser.close();
	console.log(`\n  video written under ${VIDEO}`);
}

await main();
