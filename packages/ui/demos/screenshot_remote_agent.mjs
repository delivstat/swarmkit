// Screenshot "Add a remote agent by card URL" on the Connections page against a REAL serve
// (design/details/a2a-interop.md "Discovery"). The serve instance has A2A enabled, and the card
// it is asked to look up is its OWN — so the capture proves the probe, the pick and the written
// skill file, not just the layout.
//
//   rm -rf /tmp/swarmkit-a2a-demo && cp -r examples/hello-swarm/workspace /tmp/swarmkit-a2a-demo \
//       && printf 'server:\n  a2a:\n    enabled: true\n    identity:\n      name: Hello desk\n' \
//          >> /tmp/swarmkit-a2a-demo/workspace.yaml
//   swarmkit serve /tmp/swarmkit-a2a-demo --port 8098 --insecure --cors-origin http://127.0.0.1:3010
//   NEXT_PUBLIC_SWARMKIT_API=http://127.0.0.1:8098 pnpm --filter @swarmkit/ui dev -p 3010
//   pnpm --filter @swarmkit/ui demo:remote-agent [outdir]

import { chromium } from "@playwright/test";

const out = process.argv[2] ?? ".";
const UI = process.env.UI_ORIGIN ?? "http://127.0.0.1:3010";
const CARD =
	process.env.CARD_URL ?? "http://127.0.0.1:8098/.well-known/agent-card.json";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("console", (m) => {
	if (m.type() === "error") console.log("console error:", m.text());
});

await page.goto(`${UI}/connections`, { waitUntil: "networkidle" });
await page.getByRole("button", { name: "Remote agent" }).click();
await page.getByLabel("Agent Card URL").fill(CARD);
await page.getByRole("button", { name: "Look up" }).click();
await page.waitForSelector("text=Skill on the card", { timeout: 15000 });
await page.waitForTimeout(500);
await page.screenshot({ path: `${out}/connections-add-remote-agent.png` });

await page.getByLabel("Skill id in this workspace").fill("hello-desk");
await page.getByRole("button", { name: "Add agent" }).click();
await page.waitForSelector("text=remote agent", { timeout: 15000 });
await page.waitForTimeout(1500);
await page.screenshot({ path: `${out}/connections-remote-agent-row.png` });

await browser.close();
console.log(
	`wrote connections-add-remote-agent.png, connections-remote-agent-row.png to ${out}`,
);
