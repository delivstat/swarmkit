// Re-record the SDLC walkthrough clips (docs/site/sdlc-example/) against a REAL serve.
//
// The published clips were hand-recorded before 1.189.0 and show a portal that has since changed:
// `Pipelines` and `Runs` are gone, `Canary`, `Triggers`, `Comprehension` and `Memory` are new. This
// re-records the chapters that are still filmable, at the same 1280x800 the originals use, so a
// replacement is a drop-in.
//
//   just build-webui                       # or the clips film a stale portal — the export is
//                                          # generated, not committed, so a stale local one is easy
//                                          # to record by accident
//   uv sync --all-packages                 # or the footer bakes in a wrong version number
//   uv run swarmkit serve examples/sdlc-pipeline/workspace --host 127.0.0.1 --port 8399
//   node packages/ui/demos/record_walkthrough.mjs [outdir] [chapter...]
//
// Output is .webm; convert with:
//   ffmpeg -i in.webm -c:v libx264 -preset slow -crf 26 -pix_fmt yuv420p -movflags +faststart out.mp4
//
// NOT covered: `pipelines` (the page was removed in 1.189.0 — nothing to film) and `flow-run`
// (a real run against live models, so re-recording it spends money and needs a decision, not a
// script). `full-tour` stitches every chapter including the removed one and needs an editorial
// call first.

import fs from "node:fs";
import { chromium } from "@playwright/test";

const OUT = process.argv[2] ?? "/tmp/walkthrough";
const BASE = process.env.SERVE_ORIGIN ?? "http://127.0.0.1:8399";
const VIEWPORT = { width: 1280, height: 800 };

// Pauses are long on purpose. A viewer is reading the screen, not scrubbing, and the hand-recorded
// originals linger noticeably longer than a script naturally would.
const READ = 2800;
const GLANCE = 1400;
const HOVER = 800;

/** Each chapter mirrors what its section on the page claims the clip shows. */
const CHAPTERS = {
	topologies: async (p, act) => {
		await act.goto("/topologies/");
		await act.beat(READ);
		await act.open('a[href="/composer/?topology=oms-stage-run"]');
		for (const view of ["structure", "relationships", "network", "canvas"]) {
			await act.tab(view);
		}
		await act.tab("structure", GLANCE);
		await act.node(/^designer/);
		await act.tab("YAML", READ + 600);
	},

	archetypes: async (p, act) => {
		await act.goto("/archetypes/");
		await act.beat(READ);
		// "Opening developer (a harness executor) and business-analyst (a model executor)."
		// Back to the list between the two: `View` navigates, and a second pick on the detail page
		// finds no rows at all — which fails as a timeout rather than as anything legible.
		for (const name of ["developer", "business-analyst"]) {
			await act.goto("/archetypes/");
			await act.beat(GLANCE);
			await act.pick(name, "/archetypes");
			await act.tab("YAML", READ);
			await act.tab("form", GLANCE);
		}
	},

	skills: async (p, act) => {
		await act.goto("/skills/");
		await act.beat(READ);
		// "Opening artifact-judge (a decision skill) and multi-party-approval-request (coordination)."
		for (const name of ["artifact-judge", "multi-party-approval-request"]) {
			await act.goto("/skills/");
			await act.beat(GLANCE);
			await act.pick(name, "/skills");
			await act.beat(READ);
		}
	},

	funnels: async (p, act) => {
		await act.goto("/funnels/");
		await act.beat(READ);
		// The design gate is the one the chapter argues about: judge -> multi-party approval.
		await act.pick("oms-design-gate");
		await act.beat(READ + 800);
		await act.goto("/funnels/");
		await act.beat(GLANCE);
		await act.pick("oms-code-review");
		await act.beat(READ);
	},

	contracts: async (p, act) => {
		await act.goto("/contracts/");
		await act.beat(READ);
		// "Opening oms-web and oms-inventory — the two contracts the design stage holds at once."
		for (const name of ["oms-web", "oms-inventory"]) {
			await act.goto("/contracts/");
			await act.beat(GLANCE);
			await act.pick(name);
			await act.beat(READ);
		}
	},

	overview: async (p, act) => {
		// The hero clip: what the portal is, before any one artifact kind.
		await act.goto("/dashboard/");
		await act.beat(READ);
		await act.goto("/topologies/");
		await act.beat(GLANCE);
		await act.goto("/skills/");
		await act.beat(GLANCE);
		await act.goto("/funnels/");
		await act.beat(GLANCE);
		await act.goto("/contracts/");
		await act.beat(GLANCE);
		await act.goto("/audit/");
		await act.beat(READ);
	},
};

/** The artifact ids a list page shows, in the order it shows them. */
async function fetchIds(path) {
	try {
		const res = await fetch(BASE + path);
		const body = await res.json();
		const items = Array.isArray(body)
			? body
			: (body[path.replace(/\//g, "")] ?? []);
		return items.map((x) =>
			typeof x === "string" ? x : (x.id ?? x.name ?? ""),
		);
	} catch {
		return [];
	}
}

function actions(p) {
	const beat = (ms = GLANCE) => p.waitForTimeout(ms);
	return {
		beat,
		async goto(path) {
			await p.goto(BASE + path, { waitUntil: "networkidle", timeout: 30000 });
		},
		/** Hover before clicking: the originals show the cursor settle before anything moves. */
		async open(selector) {
			const el = p.locator(selector).first();
			await el.scrollIntoViewIfNeeded();
			await el.hover();
			await beat(HOVER);
			await el.click();
			await p.waitForLoadState("networkidle");
			await beat(READ + 400);
		},
		async tab(name, ms = READ) {
			const btn = p.getByRole("button", { name, exact: true });
			if (!(await btn.count())) return false;
			await btn.first().hover();
			await beat(400);
			await btn.first().click();
			await beat(ms);
			return true;
		},
		async node(pattern) {
			const el = p.locator("button").filter({ hasText: pattern }).first();
			if (!(await el.count())) return false;
			await el.hover();
			await beat(HOVER);
			await el.click();
			await beat(READ);
			return true;
		},
		/** Open a named artifact from a list page, whatever shape its row happens to be.
		 *
		 * The list pages do not agree. Funnels and contracts make the NAME the button; archetypes
		 * and skills put the name in the row and a generic `View` beside it. Both are tried rather
		 * than one being assumed, because guessing wrong here records a clip of a list page nobody
		 * clicked — which looks like a working recording until someone watches it.
		 */
		async pick(name, listPath) {
			// Where the rows carry a generic `View`, the name is not adjacent to the button — the
			// text beside it is the skill's category. The list order does match the API's, so the
			// position is what identifies the row. Derived from the API rather than scraped, so a
			// layout change cannot silently shift which artifact gets opened.
			const views = p.getByRole("button", { name: "View", exact: true });
			const count = await views.count();
			if (count && listPath) {
				const ids = await fetchIds(listPath);
				const idx = ids.indexOf(name);
				if (idx >= 0 && idx < count) {
					const btn = views.nth(idx);
					await btn.scrollIntoViewIfNeeded();
					await btn.hover();
					await beat(HOVER);
					await btn.click();
					await beat(GLANCE);
					return true;
				}
				console.log(
					`      (${name} not at a clickable position in ${listPath})`,
				);
			}
			for (const build of [
				() => p.getByRole("link", { name, exact: true }),
				() => p.getByRole("button", { name, exact: true }),
				() => p.getByText(name, { exact: true }),
			]) {
				const el = build().first();
				if (await el.count()) {
					await el.scrollIntoViewIfNeeded();
					await el.hover();
					await beat(HOVER);
					await el.click();
					await beat(GLANCE);
					return true;
				}
			}
			console.log(`      (nothing named ${name} on this page)`);
			return false;
		},
	};
}

const wanted = process.argv.slice(3).filter((a) => a in CHAPTERS);
const chapters = wanted.length ? wanted : Object.keys(CHAPTERS);
fs.mkdirSync(OUT, { recursive: true });

for (const name of chapters) {
	const dir = `${OUT}/${name}`;
	fs.rmSync(dir, { recursive: true, force: true });
	const browser = await chromium.launch();
	const ctx = await browser.newContext({
		viewport: VIEWPORT,
		recordVideo: { dir, size: VIEWPORT },
	});
	const page = await ctx.newPage();
	const started = Date.now();
	try {
		await CHAPTERS[name](page, actions(page));
	} catch (err) {
		console.log(`  ${name}: FAILED — ${String(err.message).slice(0, 90)}`);
	}
	await ctx.close();
	await browser.close();
	const file = fs.readdirSync(dir).find((f) => f.endsWith(".webm"));
	const secs = ((Date.now() - started) / 1000).toFixed(0);
	console.log(`  ${name.padEnd(12)} ${secs}s  ${dir}/${file}`);
}
