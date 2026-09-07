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
import { mkdirSync, writeFileSync } from "node:fs";
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

/** How long each screen holds. Long enough to read the caption, short enough to keep moving. */
const HOLD_MS = 3200;

/**
 * What each screen is *for*, in a sentence a viewer can read in three seconds.
 *
 * Deliberately claims rather than labels: "Gates" tells nobody anything, while "a run parked on a
 * human decision" is the thing worth understanding.
 */
const CAPTIONS = {
	dashboard: "A swarm is a workspace: topologies, skills, runs — all of it data.",
	topologies: "Topologies are YAML files the runtime interprets. Nothing is generated.",
	composer: "Every agent's model, skills and children — read back from the file.",
	canvas: "The same file, as a graph. GATED means a human must approve before it proceeds.",
	gates: "A run paused for a person. It is checkpointed — nothing is held open.",
	jobs: "Every run, with status and cost.",
	skills: "Skills are the only way an agent gains a capability.",
	archetypes: "Archetypes are reusable agents — model, prompt, scopes.",
	connections: "What the workspace can reach, and whether each credential resolves right now.",
	audit: "Append-only. No agent has an update or delete path, ever.",
};

/** Draw the caption over the page. Removed and redrawn per screen so it never stacks. */
async function caption(page, text) {
	await page.evaluate((line) => {
		document.getElementById("swarmkit-caption")?.remove();
		const el = document.createElement("div");
		el.id = "swarmkit-caption";
		el.textContent = line;
		el.style.cssText = [
			"position:fixed", "left:50%", "bottom:40px", "transform:translateX(-50%)",
			"max-width:min(1100px,86vw)", "padding:14px 26px", "z-index:2147483647",
			"background:rgba(10,10,12,.92)", "color:#f4f4f5", "border:1px solid rgba(255,255,255,.14)",
			"border-radius:10px", "font:500 20px/1.45 system-ui,sans-serif", "text-align:center",
			"box-shadow:0 8px 30px rgba(0,0,0,.55)", "pointer-events:none",
		].join(";");
		document.body.appendChild(el);
	}, text);
}

const stamp = (ms) => {
	const t = Math.max(0, ms);
	const h = String(Math.floor(t / 3600000)).padStart(2, "0");
	const m = String(Math.floor(t / 60000) % 60).padStart(2, "0");
	const s = String(Math.floor(t / 1000) % 60).padStart(2, "0");
	return `${h}:${m}:${s}.${String(t % 1000).padStart(3, "0")}`;
};

/** WebVTT, for a player that loads a track rather than relying on the burned-in text. */
const toVtt = (cues) =>
	`WEBVTT\n\n${cues
		.map(([a, b, text], i) => `${i + 1}\n${stamp(a)} --> ${stamp(b)}\n${text}\n`)
		.join("\n")}`;

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
	// One continuous pass, paced for a viewer rather than a test, with a caption burned in.
	//
	// Burned in rather than a sidecar track: this loops on a docs page and in a deck, where a
	// caption file is not loaded and the video has to explain itself with the sound off. A .vtt is
	// written alongside anyway, for a player that wants one.
	const filmed = await browser.newContext({
		viewport: VIEWPORT,
		colorScheme: "dark",
		recordVideo: { dir: VIDEO, size: VIEWPORT },
	});
	const stage = await filmed.newPage();
	const cues = [];
	let elapsed = 0;

	for (const [name, path, why, tab] of PAGES) {
		await stage.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded" });
		await settle(stage);
		if (tab) {
			await stage.getByRole("button", { name: tab, exact: true }).click().catch(() => {});
			await stage.waitForTimeout(1200);
		}
		await caption(stage, CAPTIONS[name] ?? why);
		const start = elapsed;
		await stage.waitForTimeout(HOLD_MS);
		elapsed += HOLD_MS;
		cues.push([start, elapsed, CAPTIONS[name] ?? why]);
	}
	await filmed.close(); // the video is only written on close
	await browser.close();

	writeFileSync(join(VIDEO, "tour.vtt"), toVtt(cues));
	console.log(`\n  video + captions written under ${VIDEO}`);
}

await main();
